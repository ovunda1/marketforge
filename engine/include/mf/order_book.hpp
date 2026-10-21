#pragma once

#include <cstddef>
#include <functional>
#include <map>
#include <optional>
#include <stdexcept>
#include <unordered_map>
#include <vector>

#include "mf/commands.hpp"
#include "mf/events.hpp"
#include "mf/types.hpp"

namespace mf {

// Thrown when the book detects a broken invariant or a broken caller contract
// (a non-increasing seq_no). Either one is a bug, never a normal reject.
class InvariantViolation : public std::logic_error {
 public:
  using std::logic_error::logic_error;
};

// True when the O(N) invariant walk runs after every command (MF_CHECK_INVARIANTS).
#ifdef MF_CHECK_INVARIANTS
inline constexpr bool kInvariantChecksEnabled = true;
#else
inline constexpr bool kInvariantChecksEnabled = false;
#endif

// Single-symbol limit order book with price-time priority.
//
// Single-threaded and deterministic: the output depends only on the sequence of
// commands. No clock, no randomness, and no iteration over unordered containers
// affects the output.
class OrderBook {
 public:
  OrderBook() = default;
  OrderBook(const OrderBook&) = delete;  // orders hold pointers into this book
  OrderBook& operator=(const OrderBook&) = delete;

  // Applies one command and appends the resulting events to events_out (it is never
  // cleared). Throws InvariantViolation if seq_no does not exceed the previous one,
  // before touching the book; this check runs in every build.
  void process(const Command& command, std::vector<Event>& events_out);

  std::optional<Price> best_bid() const;
  std::optional<Price> best_ask() const;

  // Aggregate open quantity and order count at a price; 0 when the level is empty.
  Qty level_qty(Side side, Price price) const;
  std::size_t level_order_count(Side side, Price price) const;
  // Order ids at a price in time priority (front of the queue first).
  std::vector<OrderId> level_orders(Side side, Price price) const;
  // Number of distinct price levels on one side.
  std::size_t level_count(Side side) const;
  // Prices of the levels on one side, best first.
  std::vector<Price> level_prices(Side side) const;

  std::size_t order_count() const { return orders_.size(); }
  // Open quantity of a resting order, or nullopt if it is not on the book.
  std::optional<Qty> leaves_qty(OrderId order_id) const;

  // Full O(N) consistency walk; throws InvariantViolation on failure. Runs after
  // every command when MF_CHECK_INVARIANTS is defined, and may be called directly.
  void check_invariants() const;

 private:
  struct Level;

  // A resting order is also its own node in its price level's FIFO queue (intrusive
  // doubly linked list), so cancel unlinks it in O(1) without a separate list node.
  struct Order {
    OrderId order_id{};
    AccountId account_id{};
    Side side{};
    Price price{};
    Qty original_qty{};  // current total quantity (a replace changes it)
    Qty leaves_qty{};
    Qty filled_qty{};
    SeqNo seq_no{};  // command that last set its time priority
    bool post_only{};
    StpMode stp{};
    Level* level{};
    Order* prev{};
    Order* next{};
  };

  struct Level {
    Price price{};
    Qty total_qty{};  // cached sum of leaves_qty over the queue
    std::size_t order_count{};
    Order* head{};  // oldest order: matched first
    Order* tail{};  // newest order
  };

  // The incoming side of a match: a new order, or a resting order re-priced by a replace.
  struct Aggressor {
    SeqNo seq_no{};
    OrderId order_id{};
    AccountId account_id{};
    Side side{};
    std::optional<Price> limit;  // nullopt for a market order
    bool post_only{};
    StpMode stp{};
    Qty leaves{};
    Qty filled{};
  };

  // begin() is the best price on both sides. std::map nodes never move, so the
  // Level* stored in each Order stays valid while other levels come and go.
  using BidLevels = std::map<Price, Level, std::greater<Price>>;
  using AskLevels = std::map<Price, Level, std::less<Price>>;

  void on_new_order(const NewOrder& order, std::vector<Event>& out);
  void on_cancel(const Cancel& cancel, std::vector<Event>& out);
  void on_replace(const Replace& replace, std::vector<Event>& out);

  // Matches the aggressor, then cancels its remainder (STP or non-GTC) or rests it.
  void execute(Aggressor& taker, TimeInForce tif, Qty original_qty, std::vector<Event>& out);
  // Returns true when self-trade prevention cancelled the aggressor's remainder.
  template <class Levels>
  bool match(Levels& opposite, Aggressor& taker, std::vector<Event>& out);
  // Read-only walk: could the aggressor be filled completely, given its STP mode?
  template <class Levels>
  bool fok_fillable(const Levels& opposite, const Aggressor& taker) const;
  // Would a limit order at `price` on `side` take liquidity?
  bool crosses_opposite(Side side, Price price) const;
  bool opposite_empty(Side side) const;
  void rest(const Aggressor& taker, Qty original_qty);
  // Unlinks a resting order, erases its level if now empty, and drops it from the index.
  void remove(Order& order);
  void erase_level(Side side, Price price);

  static void append(Level& level, Order& order);
  static void unlink(Level& level, Order& order);

  template <class Levels>
  std::size_t check_side(const Levels& levels, Side side) const;
  const Level* find_level(Side side, Price price) const;

  BidLevels bids_;
  AskLevels asks_;
  // Owns every resting order. unordered_map is node-based: element addresses are
  // stable across rehashing, so the intrusive list pointers into it stay valid.
  // It is only used for lookup and counting, never iterated to produce output.
  std::unordered_map<OrderId, Order> orders_;
  std::optional<SeqNo> last_seq_no_;
  TradeId next_trade_id_{1};
};

}  // namespace mf
