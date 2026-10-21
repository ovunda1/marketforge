#pragma once

#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <type_traits>

#include "mf/object_pool.hpp"
#include "mf/types.hpp"

namespace mf {

// One side of a book's price levels (M6): a flat array of kWindow tick slots around the
// best price, plus a sorted overflow map for levels outside it.
//
// A price inside the window is found by subtracting the window's base: no tree walk and
// no allocation (levels come from a pool). An occupancy bitmap finds the next level after
// the best is erased in a few word scans. Levels outside the window (far from the best)
// live in the overflow map, which is slower but rare.
//
// Invariants (check() verifies them): whenever the side is not empty its best level is in
// the window, and every overflow level is worse than every window price (bids: below the
// window, asks: above it), so best-first order is the window from the best, then the
// overflow in order. The window moves (re-centres) when a new best lands beyond it, when
// the window empties, and when the best drifts into the window's far quarter; a move
// shifts levels between window and overflow but never moves a Level object, so pointers
// to levels (each resting order holds one) stay valid until the level is erased.
//
// Level must be trivially destructible with a `Price price` member.
template <class Level, bool kBid, std::size_t kWindow = 4096>
class PriceLevels {
  static_assert(kWindow >= 64 && (kWindow & (kWindow - 1)) == 0);

 public:
  PriceLevels() = default;
  PriceLevels(const PriceLevels&) = delete;
  PriceLevels& operator=(const PriceLevels&) = delete;

  bool empty() const { return size_ == 0; }
  std::size_t size() const { return size_; }

  // The best level, or nullptr when empty.
  Level* first() { return best_; }
  const Level* first() const { return best_; }

  // The level after `level` in best-first order, or nullptr after the last.
  const Level* after(const Level& level) const {
    if (in_window(level.price)) {
      const std::size_t i = slot(level.price);
      const std::size_t j = kBid ? (i == 0 ? kWindow : scan(i - 1))
                                 : (i + 1 == kWindow ? kWindow : scan(i + 1));
      if (j != kWindow) return slots_[j];
      return outside_.empty() ? nullptr : outside_.begin()->second;
    }
    auto it = outside_.find(level.price);
    ++it;
    return it == outside_.end() ? nullptr : it->second;
  }
  Level* after(Level& level) {
    return const_cast<Level*>(static_cast<const PriceLevels*>(this)->after(level));
  }

  const Level* find(Price price) const {
    if (in_window(price)) return slots_[slot(price)];
    const auto it = outside_.find(price);
    return it == outside_.end() ? nullptr : it->second;
  }
  Level* find(Price price) {
    return const_cast<Level*>(static_cast<const PriceLevels*>(this)->find(price));
  }

  // The level at `price`, created empty (only its price set) if there is none.
  Level& get_or_create(Price price) {
    if (size_ == 0) place_window(price);
    if (in_window(price)) return create_in_window(price);
    if (worse_than_window(price)) {
      const auto [it, inserted] = outside_.try_emplace(price, nullptr);
      if (inserted) {
        it->second = pool_.create(Level{.price = price});
        ++size_;
      }
      return *it->second;
    }
    // Better than every window price: it becomes the best, so the window moves to it.
    place_window(price);
    return create_in_window(price);
  }

  // Removes a level of this side (its orders must be gone) and destroys it.
  void erase(Level& level) {
    const Price price = level.price;
    const bool was_best = &level == best_;
    if (in_window(price)) {
      const std::size_t i = slot(price);
      slots_[i] = nullptr;
      bits_[i / 64] &= ~(std::uint64_t{1} << (i % 64));
    } else {
      outside_.erase(price);
    }
    pool_.destroy(&level);
    --size_;
    if (!was_best) return;
    best_ = nullptr;
    if (size_ == 0) return;
    const std::size_t from = slot(price);  // the old best was in the window
    const std::size_t j = scan(from);
    if (j == kWindow) {
      place_window(outside_.begin()->first);  // the window emptied: move to the overflow
      return;
    }
    best_ = slots_[j];
    // The best drifted into the window's far quarter: re-centre so that the levels
    // behind it fit in the window again.
    if (kBid ? j < kWindow / 4 : j >= kWindow - kWindow / 4) place_window(best_->price);
  }

  // Full consistency walk; throws std::logic_error on failure.
  void check() const {
    std::size_t counted = 0;
    const Level* best = nullptr;
    for (std::size_t i = 0; i < kWindow; ++i) {
      const bool bit = (bits_[i / 64] >> (i % 64)) & 1;
      if (bit != (slots_[i] != nullptr)) fail("bitmap disagrees with slot " + std::to_string(i));
      if (slots_[i] == nullptr) continue;
      if (slots_[i]->price != lo_ + static_cast<Price>(i)) fail("level in the wrong slot");
      ++counted;
      if (best == nullptr || better(slots_[i]->price, best->price)) best = slots_[i];
    }
    for (const auto& [price, level] : outside_) {
      if (level == nullptr || level->price != price) fail("overflow key does not match its level");
      if (!worse_than_window(price)) fail("overflow level not worse than the window");
      ++counted;
    }
    if (counted != size_) fail("size does not match the levels held");
    if (best_ != best) fail("cached best is not the best level");
    if (size_ != 0 && best_ == nullptr) fail("levels but no best in the window");
    if (lo_ < 0 || lo_ > kMaxLo) fail("window base out of range");
  }

 private:
  static constexpr std::size_t kWords = kWindow / 64;
  static constexpr Price kMaxLo = std::numeric_limits<Price>::max() - static_cast<Price>(kWindow) + 1;
  using Compare = std::conditional_t<kBid, std::greater<Price>, std::less<Price>>;

  static bool better(Price a, Price b) { return kBid ? a > b : a < b; }
  [[noreturn]] static void fail(const std::string& what) {
    throw std::logic_error(std::string(kBid ? "bid" : "ask") + " levels: " + what);
  }

  // lo_ >= 0 and price > 0 keep price - lo_ from overflowing.
  bool in_window(Price price) const {
    return price >= lo_ && static_cast<std::uint64_t>(price - lo_) < kWindow;
  }
  bool worse_than_window(Price price) const {
    return kBid ? price < lo_ : price >= lo_ && !in_window(price);
  }
  std::size_t slot(Price price) const { return static_cast<std::size_t>(price - lo_); }

  // The occupied slot nearest `from` in the worse direction (bids: down, asks: up),
  // `from` included; kWindow if there is none.
  std::size_t scan(std::size_t from) const {
    std::size_t w = from / 64;
    const unsigned bit = from % 64;
    if constexpr (kBid) {
      std::uint64_t m = bits_[w] & (bit == 63 ? ~std::uint64_t{0} : (std::uint64_t{1} << (bit + 1)) - 1);
      for (;;) {
        if (m != 0) return w * 64 + 63 - static_cast<std::size_t>(std::countl_zero(m));
        if (w == 0) return kWindow;
        m = bits_[--w];
      }
    } else {
      std::uint64_t m = bits_[w] & (~std::uint64_t{0} << bit);
      for (;;) {
        if (m != 0) return w * 64 + static_cast<std::size_t>(std::countr_zero(m));
        if (++w == kWords) return kWindow;
        m = bits_[w];
      }
    }
  }

  Level& create_in_window(Price price) {
    const std::size_t i = slot(price);
    if (slots_[i] == nullptr) {
      slots_[i] = pool_.create(Level{.price = price});
      bits_[i / 64] |= std::uint64_t{1} << (i % 64);
      ++size_;
      if (best_ == nullptr || better(price, best_->price)) best_ = slots_[i];
    }
    return *slots_[i];
  }

  // Moves the window so that `anchor` (the best price, or a price about to become it)
  // sits a quarter of the way in from the better edge, leaving three quarters of the
  // window for the levels behind it. Window levels that fall outside go to the overflow;
  // overflow levels that fall inside come back. Level objects never move.
  void place_window(Price anchor) {
    const Price quarter = static_cast<Price>(kWindow / 4);
    Price lo = kBid ? anchor - 3 * quarter : anchor - quarter;
    if (anchor < 3 * quarter) lo = std::max<Price>(lo, 0);
    lo = std::clamp<Price>(lo, 0, kMaxLo);

    // Everything in the old window goes to the overflow first...
    for (std::size_t w = 0; w < kWords; ++w) {
      for (std::uint64_t m = bits_[w]; m != 0; m &= m - 1) {
        const std::size_t i = w * 64 + static_cast<std::size_t>(std::countr_zero(m));
        outside_.emplace(slots_[i]->price, slots_[i]);
        slots_[i] = nullptr;
      }
      bits_[w] = 0;
    }
    lo_ = lo;
    // ...then every overflow level inside the new window comes back.
    const Price hi = lo_ + (static_cast<Price>(kWindow) - 1);  // cannot overflow: lo_ <= kMaxLo
    auto it = kBid ? outside_.lower_bound(hi) : outside_.lower_bound(lo_);
    while (it != outside_.end() && in_window(it->first)) {
      const std::size_t i = slot(it->first);
      slots_[i] = it->second;
      bits_[i / 64] |= std::uint64_t{1} << (i % 64);
      it = outside_.erase(it);
    }
    best_ = nullptr;
    if (size_ != 0) {
      const std::size_t j = kBid ? scan(kWindow - 1) : scan(0);
      best_ = j == kWindow ? nullptr : slots_[j];
    }
  }

  std::array<Level*, kWindow> slots_{};
  std::array<std::uint64_t, kWords> bits_{};
  Price lo_{};
  std::map<Price, Level*, Compare> outside_;
  ObjectPool<Level, 1024> pool_;
  Level* best_{};
  std::size_t size_{};
};

}  // namespace mf
