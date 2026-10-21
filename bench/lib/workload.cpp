#include "workload.hpp"

#include <algorithm>
#include <unordered_map>

#include "mf/checksum.hpp"
#include "mf/engine.hpp"
#include "mf/journal.hpp"
#include "mf/text_format.hpp"

namespace mf::bench {

namespace {

__extension__ typedef unsigned __int128 U128;

std::uint64_t splitmix64(std::uint64_t& x) {
  std::uint64_t z = (x += 0x9e3779b97f4a7c15ULL);
  z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
  z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
  return z ^ (z >> 31);
}

std::uint64_t rotl(std::uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }

// Roughly 2025 in ns since the epoch; only differences matter (the rate limiter).
constexpr Timestamp kStart = 1'760'000'000'000'000'000;

struct Params {
  Price start_mid;
  Price min_mid;
  Price max_mid;
  std::int64_t band_bps;
  std::size_t target_resting;  // above this, the next command is a cancel
  Price max_cross;             // how far an aggressive price reaches past the mid
};

constexpr Params kNarrow{.start_mid = 10'000, .min_mid = 10'000, .max_mid = 10'000,
                         .band_bps = 500, .target_resting = 1'000, .max_cross = 3};
constexpr Params kWide{.start_mid = 100'000, .min_mid = 80'000, .max_mid = 120'000,
                       .band_bps = 5'000, .target_resting = 4'000, .max_cross = 20};

class Generator {
 public:
  Generator(Profile profile, std::uint64_t seed)
      : profile_(profile), p_(profile == Profile::Narrow ? kNarrow : kWide), rng_(seed),
        mid_(p_.start_mid) {}

  Workload run(std::size_t n) {
    w_.profile = profile_;
    w_.commands.reserve(n + 2);
    w_.stats.min_price = p_.start_mid;
    w_.stats.max_price = p_.start_mid;

    // Every limit set, all generous: the gate runs each check and the flow passes.
    emit(SetInstrumentLimits{.limits = {.max_qty = 1'000'000, .band_bps = p_.band_bps,
                                        .reference_price = p_.start_mid}},
         false);
    emit(SetAccountLimits{.limits = {.max_notional = 1'000'000'000'000'000,
                                     .position_limit = 1'000'000'000'000,
                                     .credit = 1'000'000'000'000'000'000,
                                     .max_open_orders = 1'000'000,
                                     .rate = RateLimit{.per_sec = 1'000'000, .burst = 1'000'000}}},
         false);
    w_.admin_commands = w_.commands.size();

    for (std::size_t i = 0; i < n; ++i) step();
    return std::move(w_);
  }

 private:
  void step() {
    if (profile_ == Profile::Wide && seq_ % 16 == 0) {
      mid_ = std::clamp<Price>(mid_ + rng_.between(-3, 3), p_.min_mid, p_.max_mid);
    }
    // Cancels scale with the book: at the target size a quarter of the flow cancels, so
    // the book settles near the target whatever the matching does; above it, every
    // command cancels.
    const std::uint64_t live = live_.size();
    if (live > 0 && (live > p_.target_resting ||
                     rng_.below(1000 * p_.target_resting) < 260 * live)) {
      ++w_.stats.cancels;
      ++w_.stats.cancels_of_resting;
      emit(Cancel{.order_id = pick_live()});
      return;
    }
    const std::uint64_t r = rng_.below(740);
    if (r < 10) {
      ++w_.stats.cancels;  // an id that is not resting: a cheap reject, kept rare
      emit(Cancel{.order_id = next_id_ + 1'000'000 + rng_.below(1'000)});
    } else if (r < 110 && live > 0) {
      replace();
    } else if (r < 550) {
      // Every random draw gets its own statement: the evaluation order of function
      // arguments is unspecified (gcc and clang differ), and the stream must not be.
      ++w_.stats.new_passive;
      const Side side = random_side();
      const bool day = rng_.chance(1, 16);
      const bool post_only = rng_.chance(1, 10);
      const Price price = passive_price(side);
      const Qty q = qty();
      new_order(side, OrderType::Limit, day ? TimeInForce::Day : TimeInForce::GTC, post_only,
                price, q);
    } else if (r < 630) {
      ++w_.stats.new_aggressive;
      const Side side = random_side();
      const Price price = aggressive_price(side);
      const Qty q = qty();
      new_order(side, OrderType::Limit, TimeInForce::GTC, false, price, q);
    } else if (r < 710) {
      ++w_.stats.new_ioc;
      const Side side = random_side();
      const Price price = aggressive_price(side);
      const Qty q = qty();
      new_order(side, OrderType::Limit, TimeInForce::IOC, false, price, q);
    } else {
      ++w_.stats.new_market;
      const Side side = random_side();
      const Qty q = rng_.between(1, 50);
      new_order(side, OrderType::Market, TimeInForce::IOC, false, 0, q);
    }
  }

  void replace() {
    const OrderId id = pick_live();
    const auto v = *shadow_.book().find(id);
    ++w_.stats.replaces;
    ++w_.stats.replaces_of_resting;
    Price price = v.price;
    Qty qty = v.qty;
    const std::uint64_t kind = rng_.below(4);
    if (kind <= 1 && v.leaves_qty > 1) {
      qty = v.qty - rng_.between(1, v.leaves_qty - 1);  // keeps priority
    } else if (kind <= 2) {
      qty = v.qty + rng_.between(1, 50);  // loses priority
    } else {
      price = passive_price(v.side);  // moves; may cross if the book has moved
    }
    note_price(price);
    emit(Replace{.order_id = id, .price = price, .qty = qty});
  }

  void new_order(Side side, OrderType type, TimeInForce tif, bool post_only, Price price, Qty qty) {
    if (type == OrderType::Limit) note_price(price);
    const std::uint64_t s = rng_.below(16);
    const StpMode stp = s == 0 ? StpMode::CancelBoth : s <= 2 ? StpMode::CancelOldest
                                                              : StpMode::CancelNewest;
    emit(NewOrder{.order_id = next_id_++, .account_id = 1 + rng_.below(64), .side = side,
                  .type = type, .tif = tif, .post_only = post_only, .stp = stp,
                  .price = price, .qty = qty});
  }

  Side random_side() { return rng_.chance(1, 2) ? Side::Buy : Side::Sell; }

  Qty qty() { return rng_.chance(1, 20) ? rng_.between(100, 1'000) : rng_.between(1, 100); }

  Price passive_price(Side side) {
    Price off = 0;
    if (profile_ == Profile::Narrow) {
      off = rng_.between(1, 10);
    } else if (rng_.chance(1, 100)) {
      off = rng_.between(10'000, 30'000);  // far from the mid: deep, rarely touched
    } else {
      off = 1 + static_cast<Price>(rng_.below(std::uint64_t{1} << rng_.below(12)));  // log-uniform
    }
    return side == Side::Buy ? mid_ - off : mid_ + off;
  }

  Price aggressive_price(Side side) {
    const Price reach = rng_.between(0, p_.max_cross);
    return side == Side::Buy ? mid_ + reach : mid_ - reach;
  }

  void note_price(Price price) {
    w_.stats.min_price = std::min(w_.stats.min_price, price);
    w_.stats.max_price = std::max(w_.stats.max_price, price);
  }

  OrderId pick_live() { return live_[rng_.below(live_.size())]; }

  // Runs the command through the shadow engine, then re-syncs the resting set for every
  // order its events mention.
  template <class C>
  void emit(C command, bool flow = true) {
    command.seq_no = ++seq_;
    if (flow) ts_ += rng_.between(1, 2'000);
    w_.commands.push_back({.command = command, .timestamp = ts_});
    events_.clear();
    shadow_.process(w_.commands.back().command, ts_, events_);
    for (const Event& e : events_) {
      std::visit(
          [&](const auto& ev) {
            using T = std::decay_t<decltype(ev)>;
            if constexpr (std::is_same_v<T, Ack> || std::is_same_v<T, Fill> ||
                          std::is_same_v<T, Cancelled> || std::is_same_v<T, Replaced>) {
              sync(ev.order_id);
            }
          },
          e);
    }
    w_.stats.max_resting = std::max<std::uint64_t>(w_.stats.max_resting, live_.size());
  }

  void sync(OrderId id) {
    const bool resting = shadow_.book().find(id).has_value();
    const auto it = pos_.find(id);
    if (resting && it == pos_.end()) {
      pos_.emplace(id, live_.size());
      live_.push_back(id);
    } else if (!resting && it != pos_.end()) {
      const std::size_t i = it->second;
      pos_.erase(it);
      if (i + 1 != live_.size()) {
        live_[i] = live_.back();
        pos_[live_[i]] = i;
      }
      live_.pop_back();
    }
  }

  Profile profile_;
  Params p_;
  Rng rng_;
  Price mid_;
  Workload w_;
  Engine shadow_;
  std::vector<Event> events_;
  SeqNo seq_{};
  Timestamp ts_{kStart};
  OrderId next_id_{1};
  // Orders resting in the shadow engine, for picking one at random. pos_ is lookup
  // only: the order of live_ depends only on the command sequence.
  std::vector<OrderId> live_;
  std::unordered_map<OrderId, std::size_t> pos_;
};

}  // namespace

std::string_view profile_name(Profile profile) {
  return profile == Profile::Narrow ? "narrow" : "wide";
}

std::optional<Profile> parse_profile(std::string_view name) {
  if (name == "narrow") return Profile::Narrow;
  if (name == "wide") return Profile::Wide;
  return std::nullopt;
}

Rng::Rng(std::uint64_t seed) {
  std::uint64_t x = seed;
  for (auto& s : s_) s = splitmix64(x);
}

std::uint64_t Rng::next() {
  const std::uint64_t result = rotl(s_[1] * 5, 7) * 9;
  const std::uint64_t t = s_[1] << 17;
  s_[2] ^= s_[0];
  s_[3] ^= s_[1];
  s_[1] ^= s_[2];
  s_[0] ^= s_[3];
  s_[2] ^= t;
  s_[3] = rotl(s_[3], 45);
  return result;
}

std::uint64_t Rng::below(std::uint64_t bound) {
  U128 m = static_cast<U128>(next()) * bound;
  auto low = static_cast<std::uint64_t>(m);
  if (low < bound) {
    const std::uint64_t threshold = (0 - bound) % bound;
    while (low < threshold) {
      m = static_cast<U128>(next()) * bound;
      low = static_cast<std::uint64_t>(m);
    }
  }
  return static_cast<std::uint64_t>(m >> 64);
}

std::int64_t Rng::between(std::int64_t lo, std::int64_t hi) {
  return lo + static_cast<std::int64_t>(below(static_cast<std::uint64_t>(hi - lo) + 1));
}

Workload generate_workload(Profile profile, std::uint64_t seed, std::size_t n) {
  Workload w = Generator(profile, seed).run(n);
  w.seed = seed;
  return w;
}

std::string command_digest(const Workload& workload) {
  Sha256 hash;
  for (const auto& c : workload.commands) {
    hash.update(encode_record(JournalRecord{.timestamp = c.timestamp, .command = c.command}));
  }
  return Sha256::hex(hash.finish());
}

std::string event_digest(const Workload& workload) {
  Engine engine;
  Sha256 hash;
  std::vector<Event> events;
  std::string text;
  for (const auto& c : workload.commands) {
    events.clear();
    engine.process(c.command, c.timestamp, events);
    text.clear();
    append_command_output(events, seq_no_of(c.command), engine.book(), text);
    hash.update(text);
  }
  hash.update(format_depth(engine.book()));
  return Sha256::hex(hash.finish());
}

}  // namespace mf::bench
