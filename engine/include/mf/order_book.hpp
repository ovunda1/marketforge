#pragma once

#include <cstddef>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <vector>

#include "mf/commands.hpp"
#include "mf/events.hpp"
#include "mf/flat_hash_map.hpp"
#include "mf/id_set.hpp"
#include "mf/object_pool.hpp"
#include "mf/price_levels.hpp"
#include "mf/types.hpp"

namespace mf {

// Thrown when the book detects a broken invariant or a broken caller contract
// (a non-increasing seq_no). Either one is a bug, never a normal reject.
class InvariantViolation : public std::logic_error {
 public:
  using std::logic_error::logic_error;
};

// The caller-contract part: a seq_no that does not exceed the previous one, or a
// timestamp earlier than the previous command's (Engine). Always detected before any
// state changes, so the book or engine is unchanged and still usable. Any other
// InvariantViolation may come after a command began changing state.
class SequenceError : public InvariantViolation {
 public:
  using InvariantViolation::InvariantViolation;
};

// True when the O(N) invariant walk runs after every command (MF_CHECK_INVARIANTS).
#ifdef MF_CHECK_INVARIANTS
inline constexpr bool kInvariantChecksEnabled = true;
#else
inline constexpr bool kInvariantChecksEnabled = false;
#endif

// Everything a book's future output depends on, as plain data: what a snapshot stores
// (docs/snapshot-format.md). Levels are best price first and orders are in time
// priority, so the FIFO queues are rebuilt exactly; level totals are recomputed.
struct BookState {
  struct RestingOrder {
    OrderId order_id{};
    AccountId account_id{};
    Qty qty{};  // current total quantity
    Qty leaves_qty{};
    Qty filled_qty{};
    SeqNo priority_seq_no{};  // command that last set its time priority
    bool post_only{};
    StpMode stp{};
    TimeInForce tif{TimeInForce::GTC};  // GTC or DAY

    bool operator==(const RestingOrder&) const = default;
  };
  struct Level {
    Price price{};
    std::vector<RestingOrder> orders;  // time priority, front of the queue first

    bool operator==(const Level&) const = default;
  };

  SeqNo last_seq_no{};  // 0: no command processed yet
  TradeId next_trade_id{1};
  std::vector<OrderId> used_ids;  // ascending
  std::vector<Level> bids;        // best (highest) first
  std::vector<Level> asks;        // best (lowest) first

  bool operator==(const BookState&) const = default;
};

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

  // A resting order, as the risk gate sees it.
  struct OrderView {
    AccountId account_id{};
    Side side{};
    Price price{};
    Qty qty{};  // current total quantity
    Qty leaves_qty{};
    Qty filled_qty{};
  };

  // Applies one command and appends the resulting events to events_out (it is never
  // cleared). Throws InvariantViolation if seq_no does not exceed the previous one,
  // before touching the book; this check runs in every build.
  //
  // market_limit caps a market order: it trades only at prices up to (buy) or down to
  // (sell) the limit and its remainder is cancelled as UNFILLED. The risk gate sets it
  // to the price-band edge. Ignored for other commands.
  void process(const OrderCommand& command, std::vector<Event>& events_out,
               std::optional<Price> market_limit = std::nullopt);

  // Records a command that does not reach process() (a risk reject or an admin
  // command), so seq_nos stay strictly increasing across both. Throws like process().
  void skip_seq_no(SeqNo seq_no);
  // Throws SequenceError unless seq_no exceeds the last one; changes nothing.
  void check_seq_no(SeqNo seq_no) const;

  // --- Checks the Engine runs in its own order (docs/risk.md) -----------------------
  // Price, quantity and type combination of a new order, as process() checks them.
  static std::optional<RejectReason> validate_new(const NewOrder& order);
  // Price, quantity, unknown order and qty-too-low of a replace, as process() checks them.
  std::optional<RejectReason> validate_replace(const Replace& replace) const;
  // True if an order with this id was acked this session.
  bool id_used(OrderId order_id) const { return used_ids_.contains(order_id); }
  std::optional<OrderView> find(OrderId order_id) const;
  // Read-only walk of the side opposite `side`, best price first: the notional that an
  // order of `qty` could trade, up to `limit` if set. Orders of `account_id` are skipped
  // (self-trade prevention can only cancel them, letting the sweep go deeper), so the
  // result is an upper bound whatever the STP mode.
  Notional sweep_notional(Side side, Qty qty, AccountId account_id,
                          std::optional<Price> limit) const;

  // --- Session and kill-switch actions, part of the current command ------------------
  // Cancels every resting order `pred` selects, bids best price first then asks best
  // price first, FIFO within a level; one Cancelled each with seq_no and reason.
  void cancel_where(SeqNo seq_no, const std::function<bool(const OrderView&, TimeInForce)>& pred,
                    CancelReason reason, std::vector<Event>& out);
  // Session open: forgets used ids except those of orders still resting.
  void reset_used_ids();

  // Visits every resting order, bids best price first then asks, FIFO within a level.
  template <class F>
  void for_each_order(F&& f) const {
    const auto walk = [&](const auto& levels) {
      for (const Level* level = levels.first(); level != nullptr; level = levels.after(*level)) {
        for (const Order* o = level->head; o != nullptr; o = o->next) {
          f(o->order_id, OrderView{.account_id = o->account_id, .side = o->side,
                                   .price = o->price, .qty = o->original_qty,
                                   .leaves_qty = o->leaves_qty, .filled_qty = o->filled_qty});
        }
      }
    };
    walk(bids_);
    walk(asks_);
  }

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
  std::optional<SeqNo> last_seq_no() const { return last_seq_no_; }
  // Open quantity of a resting order, or nullopt if it is not on the book.
  std::optional<Qty> leaves_qty(OrderId order_id) const;

  // Copies the complete state, for snapshots. O(N).
  BookState state() const;
  // Builds a book from a state. Throws InvariantViolation if the state is inconsistent
  // (unsorted or empty levels, bad quantities, duplicate ids, a resting id missing from
  // used_ids, a crossed book). Always validates, whatever MF_CHECK_INVARIANTS says.
  static std::unique_ptr<OrderBook> from_state(const BookState& state);

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
    TimeInForce tif{TimeInForce::GTC};  // GTC or DAY
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
    TimeInForce tif{TimeInForce::GTC};  // what a remainder rests as: GTC or DAY
    Qty leaves{};
    Qty filled{};
  };

  // first() is the best price on both sides: a flat window of tick slots around it,
  // ordered overflow beyond (price_levels.hpp). Level objects never move, so the Level*
  // stored in each Order stays valid while other levels come and go.
  using BidLevels = PriceLevels<Level, true>;
  using AskLevels = PriceLevels<Level, false>;

  void on_new_order(const NewOrder& order, std::optional<Price> market_limit,
                    std::vector<Event>& out);
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
  // Drops an unlinked order from the index and returns it to the pool.
  void release(Order& order);
  void erase_level(Side side, Level& level);

  static void append(Level& level, Order& order);
  static void unlink(Level& level, Order& order);

  template <class Levels>
  std::size_t check_side(const Levels& levels, Side side) const;
  const Level* find_level(Side side, Price price) const;

  BidLevels bids_;
  AskLevels asks_;
  // Every resting order lives in the pool: no heap allocation per order once the pool
  // has grown to the book's peak size, and a slot never moves, so the intrusive list
  // pointers stay valid. orders_ indexes them by id in one flat array (no node per
  // entry); it is only used for lookup and counting, never iterated to produce output.
  ObjectPool<Order> pool_;
  FlatHashMap<OrderId, Order*> orders_;
  // Ids of every order acked this session: an id is never reused, even after the order
  // filled or was cancelled. A rejected order was never accepted, so its id stays free.
  // Lookup only, never iterated to produce output (snapshots sort it). Reset at session
  // open (keeping resting ids), which bounds memory to one session's orders. A paged
  // bitmap (id_set.hpp): ids are mostly sequential, so a new id's bit shares a cache
  // line with the previous one's.
  IdSet used_ids_;
  std::optional<SeqNo> last_seq_no_;
  TradeId next_trade_id_{1};
};

}  // namespace mf
