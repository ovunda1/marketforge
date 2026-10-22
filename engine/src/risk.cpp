#include "mf/risk.hpp"

#include <algorithm>
#include <limits>
#include <string>
#include <type_traits>

namespace mf {

namespace {

[[noreturn]] void fail(const std::string& what) { throw InvariantViolation("risk state: " + what); }

bool can_rest(const NewOrder& o) {
  return o.type == OrderType::Limit &&
         (o.tif == TimeInForce::GTC || o.tif == TimeInForce::Day);
}

Notional capacity(const RateLimit& rate) { return Notional{rate.burst} * kTokenScale; }

template <class T, class Key>
void check_ascending(const std::vector<T>& items, Key key, const char* what) {
  for (std::size_t i = 1; i < items.size(); ++i) {
    if (key(items[i - 1]) >= key(items[i])) fail(std::string(what) + " not strictly ascending");
  }
}

}  // namespace

bool limits_valid(const InstrumentLimits& l) {
  return (!l.max_qty || *l.max_qty >= 1) &&
         (!l.band_bps || (*l.band_bps >= 0 && *l.band_bps <= kMaxBandBps)) &&
         (!l.reference_price || *l.reference_price >= 1);
}

bool limits_valid(const AccountLimits& l) {
  const auto non_negative = [](const std::optional<std::int64_t>& v) { return !v || *v >= 0; };
  return non_negative(l.max_notional) && non_negative(l.position_limit) &&
         non_negative(l.credit) && non_negative(l.max_open_orders) &&
         (!l.rate || (l.rate->per_sec >= 1 && l.rate->per_sec <= kMaxRate &&
                      l.rate->burst >= 1 && l.rate->burst <= kMaxRate));
}

bool transition_allowed(SessionState from, SessionState to) {
  using enum SessionState;
  switch (to) {
    case PreOpen: return from == Closed;
    case Continuous: return from == PreOpen || from == Halted;
    case Halted: return from == Continuous;
    case Closed: return from != Closed;
  }
  return false;
}

// --- Checks ---------------------------------------------------------------------------

RiskGate::NewOrderDecision RiskGate::check_new(const NewOrder& o, Timestamp now,
                                               const OrderBook& book) {
  const auto reject = [](RejectReason r) { return NewOrderDecision{.reject = r}; };
  if (session_ != SessionState::Continuous) return reject(RejectReason::MarketClosed);
  const Account* acct = find_account(o.account_id);
  if (global_kill_ || (acct != nullptr && acct->killed)) {
    return reject(RejectReason::KillSwitch);
  }
  const AccountLimits* limits = limits_for(o.account_id);
  if (limits && limits->rate && !take_token(o.account_id, *limits->rate, now)) {
    return reject(RejectReason::RateLimit);
  }
  if (const auto r = OrderBook::validate_new(o)) return reject(*r);
  if (book.id_used(o.order_id)) return reject(RejectReason::DuplicateOrderId);
  if (instrument_.max_qty && o.qty > *instrument_.max_qty) return reject(RejectReason::MaxQty);

  const bool market = o.type == OrderType::Market;
  std::optional<Price> market_limit;
  if (instrument_.band_bps) {
    const auto ref = reference(book);
    if (market) {
      // Nothing to cap the order at: it could sweep the whole side.
      if (!ref) return reject(RejectReason::PriceBand);
      market_limit = band_edge(o.side, *ref);
    } else if (ref && !in_band(o.price, *ref)) {
      return reject(RejectReason::PriceBand);
    }
  }
  if (!limits) return {.market_limit = market_limit};

  // A market order's notional is what it could trade at most: a walk of the book.
  Notional notional = 0;
  if (limits->max_notional || limits->credit) {
    notional = market ? book.sweep_notional(o.side, o.qty, o.account_id, market_limit)
                      : Notional{o.price} * o.qty;
  }
  if (limits->max_notional && notional > *limits->max_notional) {
    return reject(RejectReason::MaxNotional);
  }
  const Exposure e = exposure(o.account_id);
  if (limits->max_open_orders && can_rest(o) && e.open_orders + 1 > *limits->max_open_orders) {
    return reject(RejectReason::MaxOpenOrders);
  }
  if (limits->position_limit) {
    const Notional pos = position(o.account_id);
    const bool over = o.side == Side::Buy
                          ? pos + e.open_buy_qty + o.qty > *limits->position_limit
                          : pos - e.open_sell_qty - o.qty < -Notional{*limits->position_limit};
    if (over) return reject(RejectReason::PositionLimit);
  }
  if (limits->credit && e.open_notional + notional > *limits->credit) {
    return reject(RejectReason::InsufficientCredit);
  }
  return {.market_limit = market_limit};
}

std::optional<RejectReason> RiskGate::check_replace(const Replace& r, Timestamp now,
                                                    const OrderBook& book) {
  if (session_ != SessionState::Continuous) return RejectReason::MarketClosed;
  // A replace names no account: the order's own checks come first, to find it.
  if (const auto reason = book.validate_replace(r)) return reason;
  const OrderBook::OrderView v = *book.find(r.order_id);
  const Account* acct = find_account(v.account_id);
  if (global_kill_ || (acct != nullptr && acct->killed)) {
    return RejectReason::KillSwitch;
  }
  const AccountLimits* limits = limits_for(v.account_id);
  if (limits && limits->rate && !take_token(v.account_id, *limits->rate, now)) {
    return RejectReason::RateLimit;
  }
  if (r.price == v.price && r.qty <= v.qty) return std::nullopt;  // only reduces risk

  if (instrument_.max_qty && r.qty > *instrument_.max_qty) return RejectReason::MaxQty;
  if (instrument_.band_bps) {
    if (const auto ref = reference(book); ref && !in_band(r.price, *ref)) {
      return RejectReason::PriceBand;
    }
  }
  if (!limits) return std::nullopt;
  const Notional notional = Notional{r.price} * r.qty;
  if (limits->max_notional && notional > *limits->max_notional) return RejectReason::MaxNotional;
  // The order's current leaves are replaced by its new leaves.
  const Qty new_leaves = r.qty - v.filled_qty;
  const Exposure e = exposure(v.account_id);
  if (limits->position_limit) {
    const Notional pos = position(v.account_id);
    const bool over =
        v.side == Side::Buy
            ? pos + e.open_buy_qty - v.leaves_qty + new_leaves > *limits->position_limit
            : pos - (e.open_sell_qty - v.leaves_qty + new_leaves) <
                  -Notional{*limits->position_limit};
    if (over) return RejectReason::PositionLimit;
  }
  if (limits->credit &&
      e.open_notional - Notional{v.price} * v.leaves_qty + Notional{r.price} * new_leaves >
          *limits->credit) {
    return RejectReason::InsufficientCredit;
  }
  return std::nullopt;
}

const AccountLimits* RiskGate::limits_for(AccountId account_id) const {
  const Account* a = find_account(account_id);
  if (a != nullptr && a->limits) return &*a->limits;
  return default_limits_ ? &*default_limits_ : nullptr;
}

RiskGate::Account& RiskGate::account(AccountId account_id) {
  if (Account* a = accounts_.find(account_id)) return *a;
  return *accounts_.try_emplace(account_id, Account{}).first;
}

std::vector<AccountId> RiskGate::account_ids() const {
  std::vector<AccountId> ids;
  ids.reserve(accounts_.size());
  accounts_.for_each([&](AccountId id, const Account&) { ids.push_back(id); });
  std::sort(ids.begin(), ids.end());  // hash order must not leak out
  return ids;
}

bool RiskGate::take_token(AccountId account_id, const RateLimit& rate, Timestamp now) {
  auto& bucket = account(account_id).bucket;
  const Notional cap = capacity(rate);
  if (!bucket) {
    bucket = RiskState::Bucket{.account_id = account_id,
                               .tokens = static_cast<std::int64_t>(cap),
                               .last_refill = now};
  }
  if (now < bucket->last_refill) {
    throw InvariantViolation("timestamp " + std::to_string(now) + " is before " +
                             std::to_string(bucket->last_refill));
  }
  // 128 bits: elapsed ns times the rate can exceed int64 after a long gap.
  const Notional refilled =
      bucket->tokens + Notional{now - bucket->last_refill} * rate.per_sec;
  bucket->tokens = static_cast<std::int64_t>(std::min(refilled, cap));
  bucket->last_refill = now;
  if (bucket->tokens < kTokenScale) return false;
  bucket->tokens -= kTokenScale;
  return true;
}

std::optional<RiskGate::Reference> RiskGate::reference(const OrderBook& book) const {
  if (last_trade_price_) return Reference{.num = *last_trade_price_, .den = 1};
  const auto bid = book.best_bid();
  const auto ask = book.best_ask();
  if (bid && ask) return Reference{.num = Notional{*bid} + *ask, .den = 2};
  if (instrument_.reference_price) return Reference{.num = *instrument_.reference_price, .den = 1};
  return std::nullopt;
}

// |price - num/den| <= (num/den) * bps / 10000, multiplied through by den * 10000 so
// everything stays integer: |price * den - num| * 10000 <= num * bps.
bool RiskGate::in_band(Price price, const Reference& ref) const {
  Notional diff = Notional{price} * ref.den - ref.num;
  if (diff < 0) diff = -diff;
  return diff * 10'000 <= ref.num * *instrument_.band_bps;
}

// The furthest price still in band: floor of the upper edge for a buy, ceiling of the
// lower edge for a sell (at least 1).
Price RiskGate::band_edge(Side side, const Reference& ref) const {
  const Notional bps = *instrument_.band_bps;
  const Notional scale = Notional{10'000} * ref.den;
  if (side == Side::Buy) {
    const Notional edge = ref.num * (10'000 + bps) / scale;
    return static_cast<Price>(std::min<Notional>(edge, std::numeric_limits<Price>::max()));
  }
  const Notional lower = ref.num * (10'000 - bps);
  const Notional edge = (lower + scale - 1) / scale;  // lower >= 0, so this is the ceiling
  return static_cast<Price>(std::max<Notional>(edge, 1));
}

// --- State updates from events ---------------------------------------------------------

void RiskGate::add_exposure(Account& a, Side side, Price price, Qty qty, int sign) {
  a.exposure.open_notional += sign * (Notional{price} * qty);
  (side == Side::Buy ? a.exposure.open_buy_qty : a.exposure.open_sell_qty) += sign * Notional{qty};
}

void RiskGate::reduce(OrderId order_id, Qty qty) {
  OpenOrder* o = open_.find(order_id);
  if (o == nullptr) throw InvariantViolation("risk: event for unknown order");
  Account& a = account(o->account_id);
  add_exposure(a, o->side, o->price, qty, -1);
  o->leaves -= qty;
  if (o->leaves == 0) {
    --a.exposure.open_orders;
    open_.erase(order_id);
  }
}

void RiskGate::apply(std::span<const Event> events, AccountId new_order_account) {
  for (const Event& event : events) {
    std::visit(
        [&](const auto& e) {
          using T = std::decay_t<decltype(e)>;
          if constexpr (std::is_same_v<T, Ack>) {
            // A market order is open only within its own command; at price 0 it adds
            // no notional while it is.
            Account& a = account(new_order_account);
            open_.try_emplace(e.order_id, OpenOrder{.account_id = new_order_account,
                                                    .side = e.side, .price = e.price,
                                                    .leaves = e.qty});
            add_exposure(a, e.side, e.price, e.qty, +1);
            ++a.exposure.open_orders;
          } else if constexpr (std::is_same_v<T, Fill>) {
            Qty& pos = account(e.account_id).position;
            pos += e.side == Side::Buy ? e.qty : -e.qty;
            reduce(e.order_id, e.qty);
          } else if constexpr (std::is_same_v<T, Trade>) {
            last_trade_price_ = e.price;
          } else if constexpr (std::is_same_v<T, Cancelled>) {
            reduce(e.order_id, e.cancelled_qty);
          } else if constexpr (std::is_same_v<T, Replaced>) {
            OpenOrder* found = open_.find(e.order_id);
            if (found == nullptr) throw InvariantViolation("risk: replace of unknown order");
            OpenOrder& o = *found;
            Account& a = account(o.account_id);
            add_exposure(a, o.side, o.price, o.leaves, -1);
            o.price = e.price;
            o.leaves = e.leaves_qty;
            add_exposure(a, o.side, o.price, o.leaves, +1);
          }
        },
        event);
  }
}

// --- Admin -----------------------------------------------------------------------------

void RiskGate::set_account_limits(std::optional<AccountId> account_id,
                                  const AccountLimits& limits) {
  if (account_id) {
    account(*account_id).limits = limits;
  } else {
    default_limits_ = limits;
  }
  refit_buckets();
}

void RiskGate::refit_buckets() {
  for (const AccountId id : account_ids()) {
    Account& account = *accounts_.find(id);
    if (!account.bucket) continue;
    const AccountLimits* limits = limits_for(id);
    if (!limits || !limits->rate) {
      account.bucket.reset();  // starts full if a rate limit returns
    } else {
      account.bucket->tokens = static_cast<std::int64_t>(
          std::min<Notional>(account.bucket->tokens, capacity(*limits->rate)));
    }
  }
}

void RiskGate::set_kill_switch(std::optional<AccountId> account_id, bool engaged) {
  if (account_id) {
    account(*account_id).killed = engaged;
  } else {
    global_kill_ = engaged;
  }
}

Qty RiskGate::position(AccountId account_id) const {
  const Account* a = find_account(account_id);
  return a == nullptr ? 0 : a->position;
}

RiskGate::Exposure RiskGate::exposure(AccountId account_id) const {
  const Account* a = find_account(account_id);
  return a == nullptr ? Exposure{} : a->exposure;
}

// --- State capture and restore -----------------------------------------------------------

RiskState RiskGate::state() const {
  RiskState s{.session = session_, .instrument = instrument_, .default_limits = default_limits_,
              .global_kill = global_kill_, .last_trade_price = last_trade_price_};
  for (const AccountId id : account_ids()) {
    const Account& a = *find_account(id);
    if (a.limits) s.account_limits.push_back({.account_id = id, .limits = *a.limits});
    if (a.killed) s.killed_accounts.push_back(id);
    if (a.position != 0) s.positions.push_back({.account_id = id, .qty = a.position});
    if (a.bucket) s.buckets.push_back(*a.bucket);
  }
  return s;
}

RiskGate RiskGate::from_state(const RiskState& s, const BookState& book) {
  RiskGate gate;
  if (static_cast<int>(s.session) > static_cast<int>(SessionState::Closed)) fail("bad session");
  if (!limits_valid(s.instrument)) fail("invalid instrument limits");
  if (s.default_limits && !limits_valid(*s.default_limits)) fail("invalid default limits");
  if (s.last_trade_price && *s.last_trade_price <= 0) fail("non-positive last trade price");
  const auto by_id = [](const auto& item) { return item.account_id; };
  check_ascending(s.account_limits, by_id, "account limits");
  check_ascending(s.killed_accounts, [](AccountId id) { return id; }, "killed accounts");
  check_ascending(s.positions, by_id, "positions");
  check_ascending(s.buckets, by_id, "buckets");

  gate.session_ = s.session;
  gate.instrument_ = s.instrument;
  gate.default_limits_ = s.default_limits;
  gate.global_kill_ = s.global_kill;
  gate.last_trade_price_ = s.last_trade_price;
  for (const auto& e : s.account_limits) {
    if (!limits_valid(e.limits)) fail("invalid limits for account " + std::to_string(e.account_id));
    gate.account(e.account_id).limits = e.limits;
  }
  for (const AccountId id : s.killed_accounts) gate.account(id).killed = true;
  for (const auto& p : s.positions) {
    if (p.qty == 0) fail("zero position stored for account " + std::to_string(p.account_id));
    gate.account(p.account_id).position = p.qty;
  }
  for (const auto& b : s.buckets) {
    const AccountLimits* limits = gate.limits_for(b.account_id);
    if (!limits || !limits->rate) {
      fail("bucket for account " + std::to_string(b.account_id) + " without a rate limit");
    }
    if (b.tokens < 0 || b.tokens > capacity(*limits->rate)) {
      fail("bucket for account " + std::to_string(b.account_id) + " out of range");
    }
    gate.account(b.account_id).bucket = b;
  }
  // Open-order exposure comes from the book, never from the stored state.
  for (const Side side : {Side::Buy, Side::Sell}) {
    for (const auto& level : side == Side::Buy ? book.bids : book.asks) {
      for (const auto& o : level.orders) {
        Account& a = gate.account(o.account_id);
        gate.open_.try_emplace(o.order_id, OpenOrder{.account_id = o.account_id, .side = side,
                                                     .price = level.price, .leaves = o.leaves_qty});
        gate.add_exposure(a, side, level.price, o.leaves_qty, +1);
        ++a.exposure.open_orders;
      }
    }
  }
  return gate;
}

void RiskGate::check_consistency(const OrderBook& book) const {
  std::map<AccountId, Exposure> rebuilt;
  std::size_t resting = 0;
  book.for_each_order([&](OrderId id, const OrderBook::OrderView& o) {
    const OpenOrder* open = open_.find(id);
    if (open == nullptr || open->leaves != o.leaves_qty || open->price != o.price ||
        open->account_id != o.account_id) {
      throw InvariantViolation("risk does not track resting order " + std::to_string(id));
    }
    Exposure& e = rebuilt[o.account_id];
    e.open_notional += Notional{o.price} * o.leaves_qty;
    (o.side == Side::Buy ? e.open_buy_qty : e.open_sell_qty) += o.leaves_qty;
    ++e.open_orders;
    ++resting;
  });
  if (resting != open_.size()) {
    throw InvariantViolation("risk tracks " + std::to_string(open_.size()) +
                             " open orders but the book holds " + std::to_string(resting));
  }
  for (const AccountId id : account_ids()) {
    const Account& a = *find_account(id);
    const auto it = rebuilt.find(id);
    if (a.exposure != (it == rebuilt.end() ? Exposure{} : it->second)) {
      throw InvariantViolation("risk exposure of account " + std::to_string(id) +
                               " does not match the book");
    }
  }
}

}  // namespace mf
