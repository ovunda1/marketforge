#include "mf/order_book.hpp"

#include <algorithm>
#include <string>
#include <type_traits>

namespace mf {

namespace {

[[noreturn]] void fail(const std::string& what) { throw InvariantViolation(what); }

// GTC and DAY remainders rest; IOC and FOK remainders are cancelled.
bool rests(TimeInForce tif) { return tif == TimeInForce::GTC || tif == TimeInForce::Day; }

}  // namespace

// --- Commands ---------------------------------------------------------------------

void OrderBook::check_seq_no(SeqNo seq_no) const {
  // Always on: the sequencer contract is cheap to check and replay depends on it.
  if (last_seq_no_ && seq_no <= *last_seq_no_) {
    fail("seq_no " + std::to_string(seq_no) + " does not exceed previous " +
         std::to_string(*last_seq_no_));
  }
}

void OrderBook::skip_seq_no(SeqNo seq_no) {
  check_seq_no(seq_no);
  last_seq_no_ = seq_no;
}

void OrderBook::process(const OrderCommand& command, std::vector<Event>& events_out,
                        std::optional<Price> market_limit) {
  skip_seq_no(std::visit([](const auto& c) { return c.seq_no; }, command));

  std::visit(
      [&](const auto& c) {
        using T = std::decay_t<decltype(c)>;
        if constexpr (std::is_same_v<T, NewOrder>) {
          on_new_order(c, market_limit, events_out);
        } else if constexpr (std::is_same_v<T, Cancel>) {
          on_cancel(c, events_out);
        } else {
          on_replace(c, events_out);
        }
      },
      command);

  if constexpr (kInvariantChecksEnabled) check_invariants();
}

std::optional<RejectReason> OrderBook::validate_new(const NewOrder& order) {
  const bool is_market = order.type == OrderType::Market;
  if (is_market ? order.price != 0 : order.price <= 0) return RejectReason::InvalidPrice;
  if (order.qty <= 0) return RejectReason::InvalidQty;
  if ((is_market && (order.tif != TimeInForce::IOC || order.post_only)) ||
      (order.post_only && !rests(order.tif))) {
    return RejectReason::InvalidOrderType;
  }
  return std::nullopt;
}

// Checks run in a fixed order (docs/order-types.md), and every failure is a Reject that
// leaves the book untouched: the order's contract can't be honored. Outcomes within the
// contract (an IOC with nothing to fill, an STP cancel) are Ack + Cancelled instead.
void OrderBook::on_new_order(const NewOrder& order, std::optional<Price> market_limit,
                             std::vector<Event>& out) {
  const auto reject = [&](RejectReason reason) {
    out.push_back(Reject{.seq_no = order.seq_no, .order_id = order.order_id, .reason = reason});
  };
  if (const auto reason = validate_new(order)) return reject(*reason);
  // Session-wide: any id acked earlier is taken, even if that order has since filled or
  // been cancelled. Checked here, not in the sequencer, so replay reproduces the reject.
  if (used_ids_.contains(order.order_id)) return reject(RejectReason::DuplicateOrderId);

  const bool is_market = order.type == OrderType::Market;
  Aggressor taker{.seq_no = order.seq_no, .order_id = order.order_id,
                  .account_id = order.account_id, .side = order.side,
                  .limit = is_market ? market_limit : std::optional<Price>{order.price},
                  .post_only = order.post_only, .stp = order.stp, .tif = order.tif,
                  .leaves = order.qty};
  if (is_market && opposite_empty(order.side)) return reject(RejectReason::NoLiquidity);
  if (order.post_only && crosses_opposite(order.side, order.price)) {
    return reject(RejectReason::PostOnlyWouldCross);
  }
  if (order.tif == TimeInForce::FOK) {
    const bool fillable = order.side == Side::Buy ? fok_fillable(asks_, taker)
                                                  : fok_fillable(bids_, taker);
    if (!fillable) return reject(RejectReason::FokNotFillable);
  }

  used_ids_.insert(order.order_id);
  out.push_back(Ack{.seq_no = order.seq_no, .order_id = order.order_id, .side = order.side,
                    .type = order.type, .tif = order.tif, .post_only = order.post_only,
                    .stp = order.stp, .price = order.price, .qty = order.qty});
  execute(taker, order.tif, order.qty, out);
}

void OrderBook::execute(Aggressor& taker, TimeInForce tif, Qty original_qty,
                        std::vector<Event>& out) {
  const bool stp_cancelled =
      taker.side == Side::Buy ? match(asks_, taker, out) : match(bids_, taker, out);
  if (taker.leaves == 0) return;
  if (stp_cancelled || !rests(tif)) {
    out.push_back(Cancelled{.seq_no = taker.seq_no, .order_id = taker.order_id,
                            .cancelled_qty = taker.leaves,
                            .reason = stp_cancelled ? CancelReason::SelfTrade
                                                    : CancelReason::Unfilled});
    return;
  }
  rest(taker, original_qty);
}

// Matches the taker against the opposite side, best price first and FIFO within a
// level, until it is filled, the book no longer crosses its limit, or self-trade
// prevention stops it. STP is checked one maker at a time, at the front of the queue:
// cancel-newest stops the sweep (caller cancels the taker), cancel-oldest cancels the
// resting order and continues, cancel-both does both.
template <class Levels>
bool OrderBook::match(Levels& opposite, Aggressor& taker, std::vector<Event>& out) {
  const auto crosses = [&](Price resting) {
    if (!taker.limit) return true;
    return taker.side == Side::Buy ? resting <= *taker.limit : resting >= *taker.limit;
  };

  bool stp_stop = false;
  while (taker.leaves > 0 && !stp_stop && !opposite.empty()) {
    const auto level_it = opposite.begin();
    Level& level = level_it->second;
    if (!crosses(level.price)) break;

    while (taker.leaves > 0 && level.head != nullptr) {
      Order& maker = *level.head;
      if (maker.account_id == taker.account_id) {
        if (taker.stp == StpMode::CancelNewest) {
          stp_stop = true;
          break;
        }
        out.push_back(Cancelled{.seq_no = taker.seq_no, .order_id = maker.order_id,
                                .cancelled_qty = maker.leaves_qty,
                                .reason = CancelReason::SelfTrade});
        unlink(level, maker);
        release(maker);  // destroys maker; not used below
        if (taker.stp == StpMode::CancelBoth) {
          stp_stop = true;
          break;
        }
        continue;
      }

      const Qty qty = std::min(taker.leaves, maker.leaves_qty);
      const TradeId trade_id = next_trade_id_++;

      maker.leaves_qty -= qty;
      maker.filled_qty += qty;
      level.total_qty -= qty;
      taker.leaves -= qty;
      taker.filled += qty;

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
                         .price = level.price, .qty = qty, .leaves_qty = taker.leaves,
                         .cum_qty = taker.filled});

      if (maker.leaves_qty == 0) {
        unlink(level, maker);
        release(maker);  // destroys maker; not used below
      }
    }
    if (level.head == nullptr) opposite.erase(level_it);
  }
  return stp_stop;
}

// Mirrors match() without mutating: other accounts' quantity counts; an own order is
// skipped under cancel-oldest (the sweep would cancel it and go on) and ends the walk
// otherwise (the sweep would cancel the taker there).
template <class Levels>
bool OrderBook::fok_fillable(const Levels& opposite, const Aggressor& taker) const {
  Qty available = 0;
  for (const auto& [price, level] : opposite) {
    if (taker.limit &&
        (taker.side == Side::Buy ? price > *taker.limit : price < *taker.limit)) {
      break;
    }
    for (const Order* o = level.head; o != nullptr; o = o->next) {
      if (o->account_id == taker.account_id) {
        if (taker.stp == StpMode::CancelOldest) continue;
        return false;
      }
      available += o->leaves_qty;
      if (available >= taker.leaves) return true;
    }
  }
  return false;
}

bool OrderBook::crosses_opposite(Side side, Price price) const {
  if (side == Side::Buy) return !asks_.empty() && asks_.begin()->first <= price;
  return !bids_.empty() && bids_.begin()->first >= price;
}

bool OrderBook::opposite_empty(Side side) const {
  return side == Side::Buy ? asks_.empty() : bids_.empty();
}

void OrderBook::rest(const Aggressor& taker, Qty original_qty) {
  const Price price = *taker.limit;  // only limit orders rest
  Order& resting = *pool_.create(Order{
      .order_id = taker.order_id, .account_id = taker.account_id, .side = taker.side,
      .price = price, .original_qty = original_qty, .leaves_qty = taker.leaves,
      .filled_qty = taker.filled, .seq_no = taker.seq_no, .post_only = taker.post_only,
      .stp = taker.stp, .tif = taker.tif});
  orders_.emplace(taker.order_id, &resting);

  Level& level = taker.side == Side::Buy
                     ? bids_.try_emplace(price, Level{.price = price}).first->second
                     : asks_.try_emplace(price, Level{.price = price}).first->second;
  append(level, resting);
}

void OrderBook::on_cancel(const Cancel& cancel, std::vector<Event>& out) {
  const auto it = orders_.find(cancel.order_id);
  if (it == orders_.end()) {
    out.push_back(Reject{.seq_no = cancel.seq_no, .order_id = cancel.order_id,
                         .reason = RejectReason::UnknownOrder});
    return;
  }
  const Qty cancelled_qty = it->second->leaves_qty;
  remove(*it->second);
  out.push_back(Cancelled{.seq_no = cancel.seq_no, .order_id = cancel.order_id,
                          .cancelled_qty = cancelled_qty, .reason = CancelReason::User});
}

// Same price: a decrease (or no change) keeps the order's place; an increase moves it to
// the back of its level. A price change is cancel + new: the order leaves the book and
// matches as an aggressor, keeping its id, filled quantity and STP mode.
std::optional<RejectReason> OrderBook::validate_replace(const Replace& replace) const {
  if (replace.price <= 0) return RejectReason::InvalidPrice;
  if (replace.qty <= 0) return RejectReason::InvalidQty;
  const auto it = orders_.find(replace.order_id);
  if (it == orders_.end()) return RejectReason::UnknownOrder;
  if (replace.qty <= it->second->filled_qty) return RejectReason::ReplaceQtyTooLow;
  return std::nullopt;
}

void OrderBook::on_replace(const Replace& replace, std::vector<Event>& out) {
  const auto reject = [&](RejectReason reason) {
    out.push_back(
        Reject{.seq_no = replace.seq_no, .order_id = replace.order_id, .reason = reason});
  };
  if (const auto reason = validate_replace(replace)) return reject(*reason);
  Order& order = *orders_.find(replace.order_id)->second;

  const Qty new_leaves = replace.qty - order.filled_qty;
  if (replace.price == order.price) {
    const bool keep_priority = replace.qty <= order.original_qty;
    Level& level = *order.level;
    level.total_qty += new_leaves - order.leaves_qty;
    order.leaves_qty = new_leaves;
    order.original_qty = replace.qty;
    if (!keep_priority) {
      unlink(level, order);
      append(level, order);
      order.seq_no = replace.seq_no;
    }
    out.push_back(Replaced{.seq_no = replace.seq_no, .order_id = replace.order_id,
                           .price = replace.price, .qty = replace.qty,
                           .leaves_qty = new_leaves, .priority_kept = keep_priority});
    return;
  }

  if (order.post_only && crosses_opposite(order.side, replace.price)) {
    return reject(RejectReason::PostOnlyWouldCross);
  }
  Aggressor taker{.seq_no = replace.seq_no, .order_id = order.order_id,
                  .account_id = order.account_id, .side = order.side,
                  .limit = replace.price, .post_only = order.post_only, .stp = order.stp,
                  .tif = order.tif, .leaves = new_leaves, .filled = order.filled_qty};
  out.push_back(Replaced{.seq_no = replace.seq_no, .order_id = replace.order_id,
                         .price = replace.price, .qty = replace.qty,
                         .leaves_qty = new_leaves, .priority_kept = false});
  remove(order);  // destroys order; only taker is used below
  execute(taker, taker.tif, replace.qty, out);
}

void OrderBook::remove(Order& order) {
  Level& level = *order.level;
  const Side side = order.side;
  const Price price = order.price;
  unlink(level, order);
  release(order);
  if (level.head == nullptr) erase_level(side, price);
}

void OrderBook::release(Order& order) {
  orders_.erase(order.order_id);
  pool_.destroy(&order);
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

std::vector<Price> OrderBook::level_prices(Side side) const {
  std::vector<Price> prices;
  if (side == Side::Buy) {
    for (const auto& [price, level] : bids_) prices.push_back(price);
  } else {
    for (const auto& [price, level] : asks_) prices.push_back(price);
  }
  return prices;
}

std::optional<Qty> OrderBook::leaves_qty(OrderId order_id) const {
  const auto it = orders_.find(order_id);
  if (it == orders_.end()) return std::nullopt;
  return it->second->leaves_qty;
}

std::optional<OrderBook::OrderView> OrderBook::find(OrderId order_id) const {
  const auto it = orders_.find(order_id);
  if (it == orders_.end()) return std::nullopt;
  const Order& o = *it->second;
  return OrderView{.account_id = o.account_id, .side = o.side, .price = o.price,
                   .qty = o.original_qty, .leaves_qty = o.leaves_qty, .filled_qty = o.filled_qty};
}

Notional OrderBook::sweep_notional(Side side, Qty qty, AccountId account_id,
                                   std::optional<Price> limit) const {
  Notional total = 0;
  Qty left = qty;
  const auto walk = [&](const auto& opposite) {
    for (const auto& [price, level] : opposite) {
      if (limit && (side == Side::Buy ? price > *limit : price < *limit)) return;
      for (const Order* o = level.head; o != nullptr; o = o->next) {
        if (o->account_id == account_id) continue;
        const Qty take = std::min(left, o->leaves_qty);
        total += Notional{price} * take;
        left -= take;
        if (left == 0) return;
      }
    }
  };
  if (side == Side::Buy) {
    walk(asks_);
  } else {
    walk(bids_);
  }
  return total;
}

// --- Session and kill-switch actions ------------------------------------------------

void OrderBook::cancel_where(SeqNo seq_no,
                             const std::function<bool(const OrderView&, TimeInForce)>& pred,
                             CancelReason reason, std::vector<Event>& out) {
  // Collect first: removing an order can erase the level being walked.
  std::vector<Order*> selected;
  const auto collect = [&](auto& levels) {
    for (auto& [price, level] : levels) {
      for (Order* o = level.head; o != nullptr; o = o->next) {
        const OrderView view{.account_id = o->account_id, .side = o->side, .price = o->price,
                             .qty = o->original_qty, .leaves_qty = o->leaves_qty,
                             .filled_qty = o->filled_qty};
        if (pred(view, o->tif)) selected.push_back(o);
      }
    }
  };
  collect(bids_);
  collect(asks_);
  for (Order* o : selected) {
    out.push_back(Cancelled{.seq_no = seq_no, .order_id = o->order_id,
                            .cancelled_qty = o->leaves_qty, .reason = reason});
    remove(*o);  // destroys *o
  }
  if constexpr (kInvariantChecksEnabled) check_invariants();
}

// Ids of resting orders stay used: a new order must not take a live order's id.
void OrderBook::reset_used_ids() {
  used_ids_.clear();
  for (const auto& [id, order] : orders_) used_ids_.insert(id);  // order-insensitive
}

// --- State capture and restore -----------------------------------------------------

BookState OrderBook::state() const {
  BookState s{.last_seq_no = last_seq_no_.value_or(0), .next_trade_id = next_trade_id_};
  s.used_ids.assign(used_ids_.begin(), used_ids_.end());
  std::sort(s.used_ids.begin(), s.used_ids.end());  // hash order must not leak out
  const auto copy_side = [](const auto& levels, std::vector<BookState::Level>& out) {
    for (const auto& [price, level] : levels) {
      BookState::Level& l = out.emplace_back(BookState::Level{.price = price});
      for (const Order* o = level.head; o != nullptr; o = o->next) {
        l.orders.push_back({.order_id = o->order_id, .account_id = o->account_id,
                            .qty = o->original_qty, .leaves_qty = o->leaves_qty,
                            .filled_qty = o->filled_qty, .priority_seq_no = o->seq_no,
                            .post_only = o->post_only, .stp = o->stp, .tif = o->tif});
      }
    }
  };
  copy_side(bids_, s.bids);
  copy_side(asks_, s.asks);
  return s;
}

std::unique_ptr<OrderBook> OrderBook::from_state(const BookState& s) {
  auto book = std::make_unique<OrderBook>();
  if (s.next_trade_id == 0) fail("state: next_trade_id must be at least 1");
  for (std::size_t i = 1; i < s.used_ids.size(); ++i) {
    if (s.used_ids[i - 1] >= s.used_ids[i]) fail("state: used_ids not strictly ascending");
  }
  book->used_ids_.insert(s.used_ids.begin(), s.used_ids.end());
  if (s.last_seq_no != 0) book->last_seq_no_ = s.last_seq_no;
  book->next_trade_id_ = s.next_trade_id;

  for (const Side side : {Side::Buy, Side::Sell}) {
    const auto& levels = side == Side::Buy ? s.bids : s.asks;
    for (std::size_t i = 0; i < levels.size(); ++i) {
      const auto& l = levels[i];
      const std::string where = "state: level " + std::to_string(l.price) + ": ";
      if (l.price <= 0) fail(where + "non-positive price");
      if (i > 0 && (side == Side::Buy ? l.price >= levels[i - 1].price
                                      : l.price <= levels[i - 1].price)) {
        fail(where + "levels not strictly best price first");
      }
      if (l.orders.empty()) fail(where + "empty level");
      Level& level = side == Side::Buy
                         ? book->bids_.try_emplace(l.price, Level{.price = l.price}).first->second
                         : book->asks_.try_emplace(l.price, Level{.price = l.price}).first->second;
      for (const auto& o : l.orders) {
        const std::string who = where + "order " + std::to_string(o.order_id) + ": ";
        if (o.leaves_qty <= 0 || o.filled_qty < 0 || o.filled_qty + o.leaves_qty != o.qty) {
          fail(who + "bad quantities");
        }
        if (o.priority_seq_no == 0 || o.priority_seq_no > s.last_seq_no) {
          fail(who + "priority seq_no outside the processed range");
        }
        if (!std::binary_search(s.used_ids.begin(), s.used_ids.end(), o.order_id)) {
          fail(who + "resting but not in used_ids");
        }
        if (!rests(o.tif)) fail(who + "time in force that cannot rest");
        if (book->orders_.contains(o.order_id)) fail(who + "duplicate resting id");
        Order& order = *book->pool_.create(Order{
            .order_id = o.order_id, .account_id = o.account_id, .side = side,
            .price = l.price, .original_qty = o.qty, .leaves_qty = o.leaves_qty,
            .filled_qty = o.filled_qty, .seq_no = o.priority_seq_no,
            .post_only = o.post_only, .stp = o.stp, .tif = o.tif});
        book->orders_.emplace(o.order_id, &order);
        append(level, order);
      }
    }
  }
  book->check_invariants();  // crossed book, cached totals
  return book;
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
      if (!rests(o->tif)) fail(who + "time in force that cannot rest");
      const auto it = orders_.find(o->order_id);
      if (it == orders_.end() || it->second != o) fail(who + "not in the order index");
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

