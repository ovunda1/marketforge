#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <map>
#include <random>
#include <vector>

#include "mf/order_book.hpp"
#include "test_helpers.hpp"

namespace mf {
namespace {

using test::events_of;
using test::run;

// Buys and sells default to different accounts so they trade; self-trade prevention is
// covered in order_types_test.cpp.
constexpr AccountId kBuyer = 1;
constexpr AccountId kSeller = 2;

NewOrder buy(SeqNo seq, OrderId id, Price price, Qty qty, AccountId acct = kBuyer) {
  return {.seq_no = seq, .order_id = id, .account_id = acct, .side = Side::Buy,
          .price = price, .qty = qty};
}

NewOrder sell(SeqNo seq, OrderId id, Price price, Qty qty, AccountId acct = kSeller) {
  return {.seq_no = seq, .order_id = id, .account_id = acct, .side = Side::Sell,
          .price = price, .qty = qty};
}

Cancel cancel(SeqNo seq, OrderId id) { return {.seq_no = seq, .order_id = id}; }

// Expected output of a command that produces exactly one event.
std::vector<Event> only_reject(SeqNo seq, OrderId id, RejectReason reason) {
  return {Reject{.seq_no = seq, .order_id = id, .reason = reason}};
}

std::vector<Event> only_cancelled(SeqNo seq, OrderId id, Qty qty) {
  return {Cancelled{.seq_no = seq, .order_id = id, .cancelled_qty = qty,
                    .reason = CancelReason::User}};
}

// --- Validation -------------------------------------------------------------------

TEST(OrderBook, LimitOrderOnEmptyBookIsAckedAndRests) {
  OrderBook book;
  const auto events = run(book, buy(1, 10, 100, 5));

  ASSERT_EQ(events.size(), 1u);
  EXPECT_EQ(events[0], (Event{Ack{.seq_no = 1, .order_id = 10, .side = Side::Buy,
                                 .price = 100, .qty = 5}}));
  EXPECT_EQ(book.best_bid(), std::optional<Price>{100});
  EXPECT_EQ(book.best_ask(), std::nullopt);
  EXPECT_EQ(book.level_qty(Side::Buy, 100), 5);
  EXPECT_EQ(book.level_order_count(Side::Buy, 100), 1u);
  EXPECT_EQ(book.leaves_qty(10), std::optional<Qty>{5});
  EXPECT_EQ(book.order_count(), 1u);
}

TEST(OrderBook, RejectsNonPositiveQuantity) {
  OrderBook book;
  EXPECT_EQ(run(book, buy(1, 10, 100, 0)),
            only_reject(1, 10, RejectReason::InvalidQty));
  EXPECT_EQ(run(book, sell(2, 11, 100, -3)),
            only_reject(2, 11, RejectReason::InvalidQty));
  EXPECT_EQ(book.order_count(), 0u);
  EXPECT_EQ(book.best_bid(), std::nullopt);
  EXPECT_EQ(book.best_ask(), std::nullopt);
}

TEST(OrderBook, RejectsNonPositivePrice) {
  OrderBook book;
  EXPECT_EQ(run(book, buy(1, 10, 0, 5)),
            only_reject(1, 10, RejectReason::InvalidPrice));
  EXPECT_EQ(run(book, sell(2, 11, -1, 5)),
            only_reject(2, 11, RejectReason::InvalidPrice));
  EXPECT_EQ(book.order_count(), 0u);
}

TEST(OrderBook, RejectsDuplicateIdOfRestingOrder) {
  OrderBook book;
  run(book, buy(1, 10, 100, 5));
  EXPECT_EQ(run(book, sell(2, 10, 200, 7)),
            only_reject(2, 10, RejectReason::DuplicateOrderId));
  EXPECT_EQ(book.leaves_qty(10), std::optional<Qty>{5});
  EXPECT_EQ(book.level_qty(Side::Buy, 100), 5);
  EXPECT_EQ(book.best_ask(), std::nullopt);
}

// Pins current M2 behavior: only ids of *resting* orders are checked. Reusing the id
// of a filled or cancelled order is accepted; the sequencer enforces session-wide
// uniqueness from M4. Change this test deliberately when that lands.
TEST(OrderBook, ReusingIdOfFilledOrCancelledOrderIsAcceptedInM2) {
  OrderBook book;
  run(book, sell(1, 10, 100, 5));
  run(book, buy(2, 11, 100, 5));  // fills order 10 completely
  ASSERT_EQ(book.leaves_qty(10), std::nullopt);

  auto events = run(book, buy(3, 10, 90, 1));
  ASSERT_EQ(events.size(), 1u);
  EXPECT_TRUE(std::holds_alternative<Ack>(events[0]));

  run(book, cancel(4, 10));
  events = run(book, buy(5, 10, 90, 2));
  ASSERT_EQ(events.size(), 1u);
  EXPECT_TRUE(std::holds_alternative<Ack>(events[0]));
  EXPECT_EQ(book.leaves_qty(10), std::optional<Qty>{2});
}

// --- Resting and aggregation ------------------------------------------------------

TEST(OrderBook, NonCrossingOrdersRestOnBothSides) {
  OrderBook book;
  run(book, buy(1, 1, 99, 5));
  run(book, buy(2, 2, 98, 5));
  run(book, sell(3, 3, 101, 5));
  run(book, sell(4, 4, 102, 5));

  EXPECT_EQ(book.best_bid(), std::optional<Price>{99});
  EXPECT_EQ(book.best_ask(), std::optional<Price>{101});
  EXPECT_EQ(book.level_count(Side::Buy), 2u);
  EXPECT_EQ(book.level_count(Side::Sell), 2u);
  EXPECT_EQ(book.order_count(), 4u);
}

TEST(OrderBook, OrdersAtSamePriceAggregateIntoOneLevel) {
  OrderBook book;
  run(book, sell(1, 1, 101, 5));
  run(book, sell(2, 2, 101, 7));
  run(book, sell(3, 3, 101, 1));

  EXPECT_EQ(book.level_count(Side::Sell), 1u);
  EXPECT_EQ(book.level_qty(Side::Sell, 101), 13);
  EXPECT_EQ(book.level_order_count(Side::Sell, 101), 3u);
  EXPECT_EQ(book.level_orders(Side::Sell, 101), (std::vector<OrderId>{1, 2, 3}));
}

// --- Matching ---------------------------------------------------------------------

TEST(OrderBook, ExactMatchFillsBothAndEmptiesBook) {
  OrderBook book;
  run(book, sell(1, 1, 100, 5));
  const auto events = run(book, buy(2, 2, 100, 5));

  const auto fills = events_of<Fill>(events);
  ASSERT_EQ(events_of<Trade>(events).size(), 1u);
  ASSERT_EQ(fills.size(), 2u);
  EXPECT_EQ(fills[0].leaves_qty, 0);
  EXPECT_EQ(fills[1].leaves_qty, 0);
  EXPECT_EQ(book.order_count(), 0u);
  EXPECT_EQ(book.best_bid(), std::nullopt);
  EXPECT_EQ(book.best_ask(), std::nullopt);
  EXPECT_EQ(book.level_count(Side::Sell), 0u);
}

TEST(OrderBook, SmallerAggressorPartiallyFillsRestingWhichKeepsItsPlace) {
  OrderBook book;
  run(book, sell(1, 1, 100, 10));
  run(book, sell(2, 2, 100, 10));
  run(book, buy(3, 3, 100, 4));

  EXPECT_EQ(book.leaves_qty(1), std::optional<Qty>{6});
  EXPECT_EQ(book.level_orders(Side::Sell, 100), (std::vector<OrderId>{1, 2}));
  EXPECT_EQ(book.level_qty(Side::Sell, 100), 16);
  EXPECT_EQ(book.leaves_qty(3), std::nullopt);
}

TEST(OrderBook, LargerAggressorRestsRemainderAtItsLimit) {
  OrderBook book;
  run(book, sell(1, 1, 100, 4));
  const auto events = run(book, buy(2, 2, 100, 10));

  const auto fills = events_of<Fill>(events);
  ASSERT_EQ(fills.size(), 2u);
  EXPECT_EQ(fills[1].order_id, 2u);
  EXPECT_EQ(fills[1].leaves_qty, 6);
  EXPECT_EQ(book.best_ask(), std::nullopt);
  EXPECT_EQ(book.best_bid(), std::optional<Price>{100});
  EXPECT_EQ(book.leaves_qty(2), std::optional<Qty>{6});
}

TEST(OrderBook, BuySweepsAskLevelsBestFirstAndStopsAtLimit) {
  OrderBook book;
  run(book, sell(1, 1, 102, 5));
  run(book, sell(2, 2, 100, 5));
  run(book, sell(3, 3, 101, 5));
  run(book, sell(4, 4, 103, 5));  // beyond the limit
  const auto events = run(book, buy(5, 5, 102, 20));

  const auto trades = events_of<Trade>(events);
  ASSERT_EQ(trades.size(), 3u);
  EXPECT_EQ(trades[0].price, 100);
  EXPECT_EQ(trades[1].price, 101);
  EXPECT_EQ(trades[2].price, 102);
  for (const auto& t : trades) EXPECT_EQ(t.qty, 5);

  EXPECT_EQ(book.best_ask(), std::optional<Price>{103});
  EXPECT_EQ(book.best_bid(), std::optional<Price>{102});
  EXPECT_EQ(book.leaves_qty(5), std::optional<Qty>{5});
}

TEST(OrderBook, SellSweepsBidLevelsHighToLow) {
  OrderBook book;
  run(book, buy(1, 1, 98, 5));
  run(book, buy(2, 2, 100, 5));
  run(book, buy(3, 3, 99, 5));
  const auto events = run(book, sell(4, 4, 99, 12));

  const auto trades = events_of<Trade>(events);
  ASSERT_EQ(trades.size(), 2u);
  EXPECT_EQ(trades[0].price, 100);
  EXPECT_EQ(trades[1].price, 99);
  EXPECT_EQ(book.best_bid(), std::optional<Price>{98});
  EXPECT_EQ(book.best_ask(), std::optional<Price>{99});
  EXPECT_EQ(book.leaves_qty(4), std::optional<Qty>{2});
}

TEST(OrderBook, TradesAtRestingPriceGivingAggressorImprovement) {
  OrderBook book;
  run(book, sell(1, 1, 100, 5));
  const auto events = run(book, buy(2, 2, 105, 5));

  const auto trades = events_of<Trade>(events);
  ASSERT_EQ(trades.size(), 1u);
  EXPECT_EQ(trades[0].price, 100);
  for (const auto& f : events_of<Fill>(events)) EXPECT_EQ(f.price, 100);
}

TEST(OrderBook, DoesNotTradeThroughLimit) {
  OrderBook book;
  run(book, sell(1, 1, 101, 5));
  const auto events = run(book, buy(2, 2, 100, 5));

  EXPECT_EQ(events.size(), 1u);  // Ack only
  EXPECT_EQ(book.best_bid(), std::optional<Price>{100});
  EXPECT_EQ(book.best_ask(), std::optional<Price>{101});
}

TEST(OrderBook, FifoTimePriorityWithinLevel) {
  OrderBook book;
  run(book, sell(1, 1, 100, 5));  // A
  run(book, sell(2, 2, 100, 5));  // B
  run(book, sell(3, 3, 100, 5));  // C
  const auto events = run(book, buy(4, 4, 100, 7));

  const auto trades = events_of<Trade>(events);
  ASSERT_EQ(trades.size(), 2u);
  EXPECT_EQ(trades[0].maker_order_id, 1u);
  EXPECT_EQ(trades[0].qty, 5);
  EXPECT_EQ(trades[1].maker_order_id, 2u);
  EXPECT_EQ(trades[1].qty, 2);
  EXPECT_EQ(book.leaves_qty(2), std::optional<Qty>{3});
  EXPECT_EQ(book.leaves_qty(3), std::optional<Qty>{5});
  EXPECT_EQ(book.level_orders(Side::Sell, 100), (std::vector<OrderId>{2, 3}));
}

TEST(OrderBook, EventOrderAndFieldsForOneMatch) {
  OrderBook book;
  run(book, sell(7, 1, 100, 10, /*acct=*/20));
  const auto events = run(book, buy(8, 2, 101, 4, /*acct=*/30));

  const std::vector<Event> expected{
      Ack{.seq_no = 8, .order_id = 2, .side = Side::Buy, .price = 101, .qty = 4},
      Trade{.seq_no = 8, .trade_id = 1, .price = 100, .qty = 4, .maker_order_id = 1,
            .taker_order_id = 2, .aggressor_side = Side::Buy},
      Fill{.seq_no = 8, .trade_id = 1, .order_id = 1, .account_id = 20, .side = Side::Sell,
           .liquidity = Liquidity::Maker, .price = 100, .qty = 4, .leaves_qty = 6,
           .cum_qty = 4},
      Fill{.seq_no = 8, .trade_id = 1, .order_id = 2, .account_id = 30, .side = Side::Buy,
           .liquidity = Liquidity::Taker, .price = 100, .qty = 4, .leaves_qty = 0,
           .cum_qty = 4},
  };
  EXPECT_EQ(events, expected);
}

TEST(OrderBook, CumQtyAccumulatesAcrossFillsOfOneOrder) {
  OrderBook book;
  run(book, buy(1, 1, 100, 10));
  run(book, sell(2, 2, 100, 3));
  const auto events = run(book, sell(3, 3, 100, 4));

  const auto fills = events_of<Fill>(events);
  ASSERT_EQ(fills.size(), 2u);
  EXPECT_EQ(fills[0].order_id, 1u);
  EXPECT_EQ(fills[0].cum_qty, 7);
  EXPECT_EQ(fills[0].leaves_qty, 3);
}

TEST(OrderBook, TradeIdsIncrementAndAreSharedByTradeAndBothFills) {
  OrderBook book;
  run(book, sell(1, 1, 100, 1));
  run(book, sell(2, 2, 101, 1));
  run(book, sell(3, 3, 102, 1));
  const auto first = run(book, buy(4, 4, 101, 2));  // trades 1 and 2

  const auto trades = events_of<Trade>(first);
  ASSERT_EQ(trades.size(), 2u);
  EXPECT_EQ(trades[0].trade_id, 1u);
  EXPECT_EQ(trades[1].trade_id, 2u);
  const auto fills = events_of<Fill>(first);
  ASSERT_EQ(fills.size(), 4u);
  EXPECT_EQ(fills[0].trade_id, 1u);
  EXPECT_EQ(fills[1].trade_id, 1u);
  EXPECT_EQ(fills[2].trade_id, 2u);
  EXPECT_EQ(fills[3].trade_id, 2u);

  // The counter continues across commands.
  const auto second = events_of<Trade>(run(book, buy(5, 5, 102, 1)));
  ASSERT_EQ(second.size(), 1u);
  EXPECT_EQ(second[0].trade_id, 3u);
}

// Orders from different accounts trade; same-account behavior is covered in
// order_types_test.cpp (self-trade prevention).
TEST(OrderBook, DifferentAccountsTrade) {
  OrderBook book;
  run(book, sell(1, 1, 100, 5, /*acct=*/42));
  const auto events = run(book, buy(2, 2, 100, 5, /*acct=*/43));
  EXPECT_EQ(events_of<Trade>(events).size(), 1u);
}

// --- Cancel -----------------------------------------------------------------------

TEST(OrderBook, CancelRemovesRestingOrderAndEmptyLevel) {
  OrderBook book;
  run(book, buy(1, 1, 100, 5));
  run(book, buy(2, 2, 99, 5));

  EXPECT_EQ(run(book, cancel(3, 1)),
            only_cancelled(3, 1, 5));
  EXPECT_EQ(book.leaves_qty(1), std::nullopt);
  EXPECT_EQ(book.level_count(Side::Buy), 1u);
  EXPECT_EQ(book.level_qty(Side::Buy, 100), 0);
  EXPECT_EQ(book.best_bid(), std::optional<Price>{99});
}

TEST(OrderBook, CancelFromMiddleOfQueuePreservesFifo) {
  OrderBook book;
  run(book, sell(1, 1, 100, 5));  // A
  run(book, sell(2, 2, 100, 5));  // B
  run(book, sell(3, 3, 100, 5));  // C
  run(book, cancel(4, 2));
  EXPECT_EQ(book.level_orders(Side::Sell, 100), (std::vector<OrderId>{1, 3}));
  EXPECT_EQ(book.level_qty(Side::Sell, 100), 10);

  const auto trades = events_of<Trade>(run(book, buy(5, 4, 100, 10)));
  ASSERT_EQ(trades.size(), 2u);
  EXPECT_EQ(trades[0].maker_order_id, 1u);
  EXPECT_EQ(trades[1].maker_order_id, 3u);
}

TEST(OrderBook, CancelPartiallyFilledOrderReportsRemainingQty) {
  OrderBook book;
  run(book, sell(1, 1, 100, 10));
  run(book, buy(2, 2, 100, 4));
  EXPECT_EQ(run(book, cancel(3, 1)),
            only_cancelled(3, 1, 6));
  EXPECT_EQ(book.order_count(), 0u);
}

TEST(OrderBook, CancelOfOrderNotOnBookIsRejected) {
  OrderBook book;
  const auto unknown = [](SeqNo seq, OrderId id) {
    return only_reject(seq, id, RejectReason::UnknownOrder);
  };
  EXPECT_EQ(run(book, cancel(1, 99)), unknown(1, 99));  // never existed

  run(book, sell(2, 1, 100, 5));
  run(book, buy(3, 2, 100, 5));
  EXPECT_EQ(run(book, cancel(4, 1)), unknown(4, 1));  // fully filled

  run(book, sell(5, 3, 100, 5));
  run(book, cancel(6, 3));
  EXPECT_EQ(run(book, cancel(7, 3)), unknown(7, 3));  // already cancelled
}

// --- Contract and invariants ------------------------------------------------------

TEST(OrderBook, InvariantChecksAreEnabledInTestBuilds) {
  EXPECT_TRUE(kInvariantChecksEnabled);
}

TEST(OrderBook, ProcessAppendsWithoutClearingOutput) {
  OrderBook book;
  const Event sentinel = Cancelled{.seq_no = 999, .order_id = 999, .cancelled_qty = 1};
  std::vector<Event> out{sentinel};
  book.process(buy(1, 1, 100, 5), out);
  book.process(sell(2, 2, 100, 5), out);

  ASSERT_EQ(out.size(), 1u + 1u + 4u);  // sentinel, Ack, then Ack+Trade+Fill+Fill
  EXPECT_EQ(out[0], sentinel);
  EXPECT_TRUE(std::holds_alternative<Ack>(out[1]));
  EXPECT_TRUE(std::holds_alternative<Ack>(out[2]));
}

TEST(OrderBook, NonIncreasingSeqNoThrowsBeforeTouchingBook) {
  OrderBook book;
  run(book, buy(5, 1, 100, 5));

  std::vector<Event> out;
  EXPECT_THROW(book.process(sell(5, 2, 100, 5), out), InvariantViolation);  // equal
  EXPECT_THROW(book.process(cancel(4, 1), out), InvariantViolation);        // lower
  EXPECT_TRUE(out.empty());
  EXPECT_EQ(book.leaves_qty(1), std::optional<Qty>{5});
  EXPECT_EQ(book.best_ask(), std::nullopt);

  // A rejected command still consumes its seq_no.
  run(book, buy(6, 3, 0, 5));
  EXPECT_THROW(book.process(buy(6, 4, 100, 1), out), InvariantViolation);
  EXPECT_NO_THROW(book.process(buy(7, 4, 100, 1), out));
}

// Every unit of every order is accounted for exactly once: filled, cancelled, or
// still resting. A Replaced event resets the order's total to its new quantity.
void expect_conservation(const OrderBook& book, const std::vector<Event>& events,
                         std::map<OrderId, Qty> total_qty) {
  std::map<OrderId, Qty> accounted;
  for (const auto& e : events) {
    if (const auto* f = std::get_if<Fill>(&e)) accounted[f->order_id] += f->qty;
    if (const auto* c = std::get_if<Cancelled>(&e)) accounted[c->order_id] += c->cancelled_qty;
    if (const auto* r = std::get_if<Replaced>(&e)) total_qty[r->order_id] = r->qty;
  }
  for (const auto& [id, qty] : total_qty) {
    const Qty resting = book.leaves_qty(id).value_or(0);
    EXPECT_EQ(accounted[id] + resting, qty) << "order " << id;
  }
}

TEST(OrderBook, QuantityIsConservedAcrossFillsAndCancels) {
  OrderBook book;
  std::vector<Event> events;
  std::map<OrderId, Qty> original;
  SeqNo seq = 0;
  const auto submit = [&](const NewOrder& o) {
    original[o.order_id] = o.qty;
    book.process(o, events);
  };
  submit(sell(++seq, 1, 101, 7));
  submit(sell(++seq, 2, 100, 3));
  submit(sell(++seq, 3, 100, 9));
  submit(buy(++seq, 4, 101, 15));
  submit(buy(++seq, 5, 99, 8));
  book.process(cancel(++seq, 1), events);
  submit(sell(++seq, 6, 98, 5));
  book.process(cancel(++seq, 5), events);

  expect_conservation(book, events, original);
}

// Seeded random flow around a mid price so many orders cross, covering every order
// type, STP mode, cancel and replace. Uses raw mt19937_64 output (fully specified by the
// standard) rather than std::uniform_int_distribution, whose output differs between
// libstdc++ and libc++. Rejected orders never enter original_qty.
std::vector<Event> random_session(std::uint64_t seed, int commands, OrderBook& book,
                                  std::map<OrderId, Qty>& original_qty) {
  std::mt19937_64 rng(seed);
  const auto uniform = [&](std::uint64_t n) { return rng() % n; };

  std::vector<Event> events;
  std::vector<OrderId> issued;
  OrderId next_id = 1;
  for (SeqNo seq = 1; seq <= static_cast<SeqNo>(commands); ++seq) {
    const std::uint64_t roll = uniform(100);
    const std::size_t before = events.size();
    if (!issued.empty() && roll < 20) {
      book.process(cancel(seq, issued[uniform(issued.size())]), events);
    } else if (!issued.empty() && roll < 35) {
      // Recent ids are more likely to still be resting.
      const std::size_t back = uniform(std::min<std::size_t>(issued.size(), 20));
      book.process(Replace{.seq_no = seq, .order_id = issued[issued.size() - 1 - back],
                           .price = 990 + static_cast<Price>(uniform(21)),
                           .qty = 1 + static_cast<Qty>(uniform(100))},
                   events);
    } else {
      const OrderId id = next_id++;
      NewOrder order{.seq_no = seq, .order_id = id, .account_id = 1 + uniform(5),
                     .side = uniform(2) == 0 ? Side::Buy : Side::Sell,
                     .stp = static_cast<StpMode>(uniform(3)),
                     .price = 990 + static_cast<Price>(uniform(21)),
                     .qty = 1 + static_cast<Qty>(uniform(100))};
      switch (uniform(8)) {
        case 0: order.tif = TimeInForce::IOC; break;
        case 1: order.tif = TimeInForce::FOK; break;
        case 2: order.type = OrderType::Market; order.tif = TimeInForce::IOC; order.price = 0; break;
        case 3: order.post_only = true; break;
        default: break;
      }
      issued.push_back(id);
      book.process(order, events);
      if (events.size() > before && std::holds_alternative<Ack>(events[before])) {
        original_qty[id] = order.qty;
      }
    }
    book.check_invariants();
  }
  return events;
}

TEST(OrderBook, RandomFlowKeepsInvariantsAndConservesQuantity) {
  OrderBook book;
  std::map<OrderId, Qty> original;
  const auto events = random_session(0xC0FFEE, 10'000, book, original);

  EXPECT_GT(events_of<Trade>(events).size(), 1000u);  // the flow actually crosses
  EXPECT_GT(events_of<Cancelled>(events).size(), 100u);
  EXPECT_GT(events_of<Replaced>(events).size(), 100u);
  std::map<CancelReason, int> cancel_reasons;
  for (const auto& c : events_of<Cancelled>(events)) ++cancel_reasons[c.reason];
  EXPECT_GT(cancel_reasons[CancelReason::Unfilled], 50);
  EXPECT_GT(cancel_reasons[CancelReason::SelfTrade], 50);
  expect_conservation(book, events, original);
}

TEST(OrderBook, SameSeedProducesIdenticalEventStream) {
  OrderBook a;
  OrderBook b;
  std::map<OrderId, Qty> original_a;
  std::map<OrderId, Qty> original_b;
  EXPECT_EQ(random_session(42, 10'000, a, original_a),
            random_session(42, 10'000, b, original_b));
}

}  // namespace
}  // namespace mf
