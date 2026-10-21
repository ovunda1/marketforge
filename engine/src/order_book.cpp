#include "mf/order_book.hpp"

#include <algorithm>
#include <string>
#include <type_traits>

namespace mf {

namespace {

[[noreturn]] void fail(const std::string& what) { throw InvariantViolation(what); }

}  // namespace

// --- Commands ---------------------------------------------------------------------

void OrderBook::process(const Command& command, std::vector<Event>& events_out) {
  // Always on: the sequencer contract is cheap to check and replay depends on it.
  const SeqNo seq_no = std::visit([](const auto& c) { return c.seq_no; }, command);
  if (last_seq_no_ && seq_no <= *last_seq_no_) {
    fail("seq_no " + std::to_string(seq_no) + " does not exceed previous " +
         std::to_string(*last_seq_no_));
  }
  last_seq_no_ = seq_no;

  std::visit(
      [&](const auto& c) {
        if constexpr (std::is_same_v<std::decay_t<decltype(c)>, NewOrder>) {
          on_new_order(c, events_out);
        } else {
          on_cancel(c, events_out);
        }
      },
      command);

  if constexpr (kInvariantChecksEnabled) check_invariants();
}

void OrderBook::on_new_order(const NewOrder& order, std::vector<Event>& out) {
  const auto reject = [&](RejectReason reason) {
    out.push_back(Reject{.seq_no = order.seq_no, .order_id = order.order_id, .reason = reason});
  };
  if (order.price <= 0) return reject(RejectReason::InvalidPrice);
  if (order.qty <= 0) return reject(RejectReason::InvalidQty);
  // Only resting orders are checked: an id of a filled or cancelled order may be reused
  // in M2. Session-wide uniqueness is enforced by the sequencer from M4.
  if (orders_.contains(order.order_id)) return reject(RejectReason::DuplicateOrderId);

  out.push_back(Ack{.seq_no = order.seq_no, .order_id = order.order_id, .side = order.side,
                    .price = order.price, .qty = order.qty});

  Qty leaves = order.qty;
  Qty filled = 0;
  if (order.side == Side::Buy) {
    match(asks_, order, leaves, filled, out);
  } else {
    match(bids_, order, leaves, filled, out);
  }
  if (leaves > 0) rest(order, leaves, filled);
}

// Matches the taker against the opposite side, best price first and FIFO within a
// level, until it is filled or the book no longer crosses its limit.
//
// Self-trades are currently allowed: orders from the same account match like any
// others. Self-trade prevention (cancel newest / oldest / both) arrives in M3.
template <class Levels>
void OrderBook::match(Levels& opposite, const NewOrder& taker, Qty& leaves, Qty& filled,
                      std::vector<Event>& out) {
  const auto crosses = [&](Price resting) {
    return taker.side == Side::Buy ? resting <= taker.price : resting >= taker.price;
  };

  while (leaves > 0 && !opposite.empty()) {
    const auto level_it = opposite.begin();
    Level& level = level_it->second;
    if (!crosses(level.price)) break;

    while (leaves > 0 && level.head != nullptr) {
      Order& maker = *level.head;
      const Qty qty = std::min(leaves, maker.leaves_qty);
      const TradeId trade_id = next_trade_id_++;

      maker.leaves_qty -= qty;
      maker.filled_qty += qty;
      level.total_qty -= qty;
      leaves -= qty;
      filled += qty;

      // Trade price is always the resting order's price.
      out.push_back(Trade{.seq_no = taker.seq_no, .trade_id = trade_id, .price = level.price,
                          .qty = qty, .maker_order_id = maker.order_id,
                          .taker_order_id = taker.order_id, .aggressor_side = taker.side});
      out.push_back(Fill{.seq_no = taker.seq_no, .trade_id = trade_id,
                         .order_id = maker.order_id, .account_id = maker.account_id,
                         .side = maker.side, .liquidity = Liquidity::Maker,
                         .price = level.price, .qty = qty, .leaves_qty = maker.leaves_qty,
                         .cum_qty = maker.filled_qty});
      out.push_back(Fill{.seq_no = taker.seq_no, .trade_id = trade_id,
                         .order_id = taker.order_id, .account_id = taker.account_id,
                         .side = taker.side, .liquidity = Liquidity::Taker,
                         .price = level.price, .qty = qty, .leaves_qty = leaves,
                         .cum_qty = filled});

      if (maker.leaves_qty == 0) {
        unlink(level, maker);
        orders_.erase(maker.order_id);  // destroys maker; not used below
      }
    }
    if (level.head == nullptr) opposite.erase(level_it);
  }
}

void OrderBook::rest(const NewOrder& order, Qty leaves, Qty filled) {
  Order& resting = orders_[order.order_id];
  resting = Order{.order_id = order.order_id, .account_id = order.account_id,
                  .side = order.side, .price = order.price, .original_qty = order.qty,
                  .leaves_qty = leaves, .filled_qty = filled, .seq_no = order.seq_no};

  Level& level = order.side == Side::Buy
                     ? bids_.try_emplace(order.price, Level{.price = order.price}).first->second
                     : asks_.try_emplace(order.price, Level{.price = order.price}).first->second;
  append(level, resting);
}

void OrderBook::on_cancel(const Cancel& cancel, std::vector<Event>& out) {
  const auto it = orders_.find(cancel.order_id);
  if (it == orders_.end()) {
    out.push_back(Reject{.seq_no = cancel.seq_no, .order_id = cancel.order_id,
                         .reason = RejectReason::UnknownOrder});
    return;
  }

  Order& order = it->second;
  const Qty cancelled_qty = order.leaves_qty;
  Level& level = *order.level;
  unlink(level, order);
  if (level.head == nullptr) erase_level(order.side, order.price);
  orders_.erase(it);

  out.push_back(Cancelled{.seq_no = cancel.seq_no, .order_id = cancel.order_id,
                          .cancelled_qty = cancelled_qty});
}

void OrderBook::erase_level(Side side, Price price) {
  if (side == Side::Buy) {
    bids_.erase(price);
  } else {
    asks_.erase(price);
  }
}

// --- Intrusive FIFO queue ---------------------------------------------------------

void OrderBook::append(Level& level, Order& order) {
  order.level = &level;
  order.prev = level.tail;
  order.next = nullptr;
  if (level.tail != nullptr) {
    level.tail->next = &order;
  } else {
    level.head = &order;
  }
  level.tail = &order;
  level.total_qty += order.leaves_qty;
  ++level.order_count;
}

// O(1): the order knows its neighbours. Subtracts the order's remaining leaves_qty
// from the level total (zero when called on a fully filled maker).
void OrderBook::unlink(Level& level, Order& order) {
  if (order.prev != nullptr) {
    order.prev->next = order.next;
  } else {
    level.head = order.next;
  }
  if (order.next != nullptr) {
    order.next->prev = order.prev;
  } else {
    level.tail = order.prev;
  }
  level.total_qty -= order.leaves_qty;
  --level.order_count;
  order.prev = order.next = nullptr;
  order.level = nullptr;
}

// --- Queries ----------------------------------------------------------------------

std::optional<Price> OrderBook::best_bid() const {
  if (bids_.empty()) return std::nullopt;
  return bids_.begin()->first;
}

std::optional<Price> OrderBook::best_ask() const {
  if (asks_.empty()) return std::nullopt;
  return asks_.begin()->first;
}

const OrderBook::Level* OrderBook::find_level(Side side, Price price) const {
  if (side == Side::Buy) {
    const auto it = bids_.find(price);
    return it == bids_.end() ? nullptr : &it->second;
  }
  const auto it = asks_.find(price);
  return it == asks_.end() ? nullptr : &it->second;
}

Qty OrderBook::level_qty(Side side, Price price) const {
  const Level* level = find_level(side, price);
  return level == nullptr ? 0 : level->total_qty;
}

std::size_t OrderBook::level_order_count(Side side, Price price) const {
  const Level* level = find_level(side, price);
  return level == nullptr ? 0 : level->order_count;
}

std::vector<OrderId> OrderBook::level_orders(Side side, Price price) const {
  std::vector<OrderId> ids;
  if (const Level* level = find_level(side, price)) {
    for (const Order* o = level->head; o != nullptr; o = o->next) ids.push_back(o->order_id);
  }
  return ids;
}

std::size_t OrderBook::level_count(Side side) const {
  return side == Side::Buy ? bids_.size() : asks_.size();
}

std::optional<Qty> OrderBook::leaves_qty(OrderId order_id) const {
  const auto it = orders_.find(order_id);
  if (it == orders_.end()) return std::nullopt;
  return it->second.leaves_qty;
}

// --- Invariants -------------------------------------------------------------------

void OrderBook::check_invariants() const {
  const std::size_t resting = check_side(bids_, Side::Buy) + check_side(asks_, Side::Sell);
  if (resting != orders_.size()) {
    fail("levels hold " + std::to_string(resting) + " orders but index holds " +
         std::to_string(orders_.size()));
  }
  if (!bids_.empty() && !asks_.empty() && bids_.begin()->first >= asks_.begin()->first) {
    fail("book crossed: bid " + std::to_string(bids_.begin()->first) + " >= ask " +
         std::to_string(asks_.begin()->first));
  }
}

// Walks one side and returns the number of orders seen.
template <class Levels>
std::size_t OrderBook::check_side(const Levels& levels, Side side) const {
  std::size_t total_orders = 0;
  for (const auto& [price, level] : levels) {
    const std::string where = "level " + std::to_string(price) + ": ";
    if (level.price != price) fail(where + "level price does not match its key");
    if (level.head == nullptr) fail(where + "empty level left on the book");

    Qty sum = 0;
    std::size_t count = 0;
    const Order* prev = nullptr;
    for (const Order* o = level.head; o != nullptr; prev = o, o = o->next) {
      const std::string who = where + "order " + std::to_string(o->order_id) + ": ";
      if (o->prev != prev) fail(who + "prev link broken");
      if (o->level != &level) fail(who + "points at the wrong level");
      if (o->side != side || o->price != price) fail(who + "wrong side or price");
      if (o->leaves_qty <= 0) fail(who + "resting with no open quantity");
      if (o->filled_qty + o->leaves_qty != o->original_qty) fail(who + "quantity not conserved");
      const auto it = orders_.find(o->order_id);
      if (it == orders_.end() || &it->second != o) fail(who + "not in the order index");
      sum += o->leaves_qty;
      ++count;
    }
    if (level.tail != prev) fail(where + "tail does not match last order");
    if (sum != level.total_qty) fail(where + "cached total_qty does not match orders");
    if (count != level.order_count) fail(where + "cached order_count does not match orders");
    total_orders += count;
  }
  return total_orders;
}

}  // namespace mf
