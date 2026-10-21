#include <gtest/gtest.h>

#include <tuple>
#include <utility>
#include <vector>

#include "mf/order_book.hpp"
#include "test_helpers.hpp"

// Market, IOC, FOK, post-only, cancel-replace and self-trade prevention (M3).
// docs/order-types.md describes each rule and its edge cases.

namespace mf {
namespace {

using test::events_of;
using test::run;

// --- Builders ---------------------------------------------------------------------

NewOrder bid(SeqNo seq, OrderId id, AccountId acct, Price price, Qty qty) {
  return {.seq_no = seq, .order_id = id, .account_id = acct, .side = Side::Buy,
          .price = price, .qty = qty};
}

NewOrder ask(SeqNo seq, OrderId id, AccountId acct, Price price, Qty qty) {
  return {.seq_no = seq, .order_id = id, .account_id = acct, .side = Side::Sell,
          .price = price, .qty = qty};
}

NewOrder market(SeqNo seq, OrderId id, AccountId acct, Side side, Qty qty) {
  return {.seq_no = seq, .order_id = id, .account_id = acct, .side = side,
          .type = OrderType::Market, .tif = TimeInForce::IOC, .price = 0, .qty = qty};
}

NewOrder ioc(NewOrder o) {
  o.tif = TimeInForce::IOC;
  return o;
}

NewOrder fok(NewOrder o) {
  o.tif = TimeInForce::FOK;
  return o;
}

NewOrder post_only(NewOrder o) {
  o.post_only = true;
  return o;
}

NewOrder with_stp(NewOrder o, StpMode mode) {
  o.stp = mode;
  return o;
}

Replace replace(SeqNo seq, OrderId id, Price price, Qty qty) {
  return {.seq_no = seq, .order_id = id, .price = price, .qty = qty};
}

Cancel cancel(SeqNo seq, OrderId id) { return {.seq_no = seq, .order_id = id}; }

// --- Expected events --------------------------------------------------------------

Event ack(const NewOrder& o) {
  return Ack{.seq_no = o.seq_no, .order_id = o.order_id, .side = o.side, .type = o.type,
             .tif = o.tif, .post_only = o.post_only, .stp = o.stp, .price = o.price,
             .qty = o.qty};
}

Event rej(SeqNo seq, OrderId id, RejectReason reason) {
  return Reject{.seq_no = seq, .order_id = id, .reason = reason};
}

Event trade(SeqNo seq, TradeId t, Price px, Qty qty, OrderId maker, OrderId taker,
            Side aggressor) {
  return Trade{.seq_no = seq, .trade_id = t, .price = px, .qty = qty,
               .maker_order_id = maker, .taker_order_id = taker,
               .aggressor_side = aggressor};
}

Event maker_fill(SeqNo seq, TradeId t, OrderId id, AccountId acct, Side side, Price px,
                 Qty qty, Qty leaves, Qty cum) {
  return Fill{.seq_no = seq, .trade_id = t, .order_id = id, .account_id = acct,
              .side = side, .liquidity = Liquidity::Maker, .price = px, .qty = qty,
              .leaves_qty = leaves, .cum_qty = cum};
}

Event taker_fill(SeqNo seq, TradeId t, OrderId id, AccountId acct, Side side, Price px,
                 Qty qty, Qty leaves, Qty cum) {
  return Fill{.seq_no = seq, .trade_id = t, .order_id = id, .account_id = acct,
              .side = side, .liquidity = Liquidity::Taker, .price = px, .qty = qty,
              .leaves_qty = leaves, .cum_qty = cum};
}

Event cxl(SeqNo seq, OrderId id, Qty qty, CancelReason reason) {
  return Cancelled{.seq_no = seq, .order_id = id, .cancelled_qty = qty, .reason = reason};
}

Event rpl(SeqNo seq, OrderId id, Price px, Qty qty, Qty leaves, bool priority_kept) {
  return Replaced{.seq_no = seq, .order_id = id, .price = px, .qty = qty,
                  .leaves_qty = leaves, .priority_kept = priority_kept};
}

constexpr auto kUnfilled = CancelReason::Unfilled;
constexpr auto kSelfTrade = CancelReason::SelfTrade;
constexpr auto B = Side::Buy;
constexpr auto S = Side::Sell;

// Full order-by-order state, for "the book did not change" checks.
using Snapshot = std::vector<std::tuple<Side, Price, std::vector<std::pair<OrderId, Qty>>>>;

Snapshot snapshot(const OrderBook& book) {
  Snapshot snap;
  for (const Side side : {Side::Buy, Side::Sell}) {
    for (const Price price : book.level_prices(side)) {
      std::vector<std::pair<OrderId, Qty>> orders;
      for (const OrderId id : book.level_orders(side, price)) {
        orders.emplace_back(id, book.leaves_qty(id).value_or(-1));
      }
      snap.emplace_back(side, price, std::move(orders));
    }
  }
  return snap;
}

// --- Market -----------------------------------------------------------------------

TEST(Market, SweepsLevelsBestFirstAndCancelsRemainder) {
  OrderBook book;
  run(book, ask(1, 1, 1, 101, 5));
  run(book, ask(2, 2, 2, 102, 3));
  const auto m = market(3, 3, 3, B, 10);

  EXPECT_EQ(run(book, m), (std::vector<Event>{
      ack(m),
      trade(3, 1, 101, 5, 1, 3, B),
      maker_fill(3, 1, 1, 1, S, 101, 5, 0, 5),
      taker_fill(3, 1, 3, 3, B, 101, 5, 5, 5),
      trade(3, 2, 102, 3, 2, 3, B),
      maker_fill(3, 2, 2, 2, S, 102, 3, 0, 3),
      taker_fill(3, 2, 3, 3, B, 102, 3, 2, 8),
      cxl(3, 3, 2, kUnfilled),
  }));
  EXPECT_EQ(book.order_count(), 0u);  // the remainder never rests
  EXPECT_EQ(book.best_bid(), std::nullopt);
}

TEST(Market, FullyFilledHasNoCancel) {
  OrderBook book;
  run(book, bid(1, 1, 1, 100, 5));
  const auto m = market(2, 2, 2, S, 5);
  EXPECT_EQ(run(book, m), (std::vector<Event>{
      ack(m),
      trade(2, 1, 100, 5, 1, 2, S),
      maker_fill(2, 1, 1, 1, B, 100, 5, 0, 5),
      taker_fill(2, 1, 2, 2, S, 100, 5, 0, 5),
  }));
}

TEST(Market, HasNoPriceLimitWithinTheSide) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 1));
  run(book, ask(2, 2, 1, 5000, 1));
  const auto events = run(book, market(3, 3, 2, B, 2));
  ASSERT_EQ(events_of<Trade>(events).size(), 2u);
  EXPECT_EQ(events_of<Trade>(events)[1].price, 5000);
}

TEST(Market, IntoEmptySideIsRejectedWithBookUnchanged) {
  OrderBook book;
  run(book, bid(1, 1, 1, 100, 5));  // same side is not liquidity for a market buy
  const auto before = snapshot(book);
  EXPECT_EQ(run(book, market(2, 2, 2, B, 3)),
            (std::vector<Event>{rej(2, 2, RejectReason::NoLiquidity)}));
  EXPECT_EQ(snapshot(book), before);
}

TEST(Market, WithNonZeroPriceIsInvalidPrice) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 5));
  auto m = market(2, 2, 2, B, 3);
  m.price = 100;
  EXPECT_EQ(run(book, m), (std::vector<Event>{rej(2, 2, RejectReason::InvalidPrice)}));
  m = market(3, 3, 2, B, 3);
  m.price = -1;
  EXPECT_EQ(run(book, m), (std::vector<Event>{rej(3, 3, RejectReason::InvalidPrice)}));
}

TEST(Market, MustBeIocAndNotPostOnly) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 5));
  auto gtc = market(2, 2, 2, B, 1);
  gtc.tif = TimeInForce::GTC;
  auto fok_market = market(3, 3, 2, B, 1);
  fok_market.tif = TimeInForce::FOK;
  const auto po_market = post_only(market(4, 4, 2, B, 1));
  EXPECT_EQ(run(book, gtc), (std::vector<Event>{rej(2, 2, RejectReason::InvalidOrderType)}));
  EXPECT_EQ(run(book, fok_market),
            (std::vector<Event>{rej(3, 3, RejectReason::InvalidOrderType)}));
  EXPECT_EQ(run(book, po_market),
            (std::vector<Event>{rej(4, 4, RejectReason::InvalidOrderType)}));
  EXPECT_EQ(book.level_qty(S, 100), 5);
}

// --- IOC --------------------------------------------------------------------------

TEST(Ioc, FillsUpToLimitAndCancelsRemainder) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 4));
  run(book, ask(2, 2, 1, 101, 4));
  const auto o = ioc(bid(3, 3, 2, 100, 6));
  EXPECT_EQ(run(book, o), (std::vector<Event>{
      ack(o),
      trade(3, 1, 100, 4, 1, 3, B),
      maker_fill(3, 1, 1, 1, S, 100, 4, 0, 4),
      taker_fill(3, 1, 3, 2, B, 100, 4, 2, 4),
      cxl(3, 3, 2, kUnfilled),
  }));
  EXPECT_EQ(book.best_bid(), std::nullopt);  // never rests
  EXPECT_EQ(book.level_qty(S, 101), 4);      // did not trade through its limit
}

TEST(Ioc, ThatDoesNotCrossIsAckedThenCancelledInFull) {
  OrderBook book;
  run(book, ask(1, 1, 1, 101, 4));
  const auto o = ioc(bid(2, 2, 2, 100, 5));
  EXPECT_EQ(run(book, o), (std::vector<Event>{ack(o), cxl(2, 2, 5, kUnfilled)}));
  EXPECT_EQ(book.order_count(), 1u);
}

TEST(Ioc, IntoEmptySideIsAckedThenCancelled) {
  OrderBook book;
  const auto o = ioc(ask(1, 1, 1, 100, 5));
  EXPECT_EQ(run(book, o), (std::vector<Event>{ack(o), cxl(1, 1, 5, kUnfilled)}));
  EXPECT_EQ(book.order_count(), 0u);
}

TEST(Ioc, FullyFilledHasNoCancel) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 5));
  const auto events = run(book, ioc(bid(2, 2, 2, 100, 5)));
  EXPECT_TRUE(events_of<Cancelled>(events).empty());
  EXPECT_EQ(events_of<Trade>(events).size(), 1u);
}

// --- FOK --------------------------------------------------------------------------

TEST(Fok, FillsExactlyAcrossLevels) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 3));
  run(book, ask(2, 2, 1, 101, 4));
  const auto o = fok(bid(3, 3, 2, 101, 7));
  EXPECT_EQ(run(book, o), (std::vector<Event>{
      ack(o),
      trade(3, 1, 100, 3, 1, 3, B),
      maker_fill(3, 1, 1, 1, S, 100, 3, 0, 3),
      taker_fill(3, 1, 3, 2, B, 100, 3, 4, 3),
      trade(3, 2, 101, 4, 2, 3, B),
      maker_fill(3, 2, 2, 1, S, 101, 4, 0, 4),
      taker_fill(3, 2, 3, 2, B, 101, 4, 0, 7),
  }));
  EXPECT_EQ(book.order_count(), 0u);
}

TEST(Fok, OneShortIsRejectedWithBookUnchanged) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 3));
  run(book, ask(2, 2, 1, 101, 4));
  const auto before = snapshot(book);
  EXPECT_EQ(run(book, fok(bid(3, 3, 2, 101, 8))),
            (std::vector<Event>{rej(3, 3, RejectReason::FokNotFillable)}));
  EXPECT_EQ(snapshot(book), before);
}

TEST(Fok, IgnoresQuantityBeyondItsLimit) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 3));
  run(book, ask(2, 2, 1, 102, 100));
  EXPECT_EQ(run(book, fok(bid(3, 3, 2, 101, 4))),
            (std::vector<Event>{rej(3, 3, RejectReason::FokNotFillable)}));
}

TEST(Fok, IntoEmptySideIsRejected) {
  OrderBook book;
  EXPECT_EQ(run(book, fok(ask(1, 1, 1, 100, 1))),
            (std::vector<Event>{rej(1, 1, RejectReason::FokNotFillable)}));
}

// Asks at 100: acct 1 (2), acct 2 (3), acct 1 (5). Acct 2 then sends FOK buys.
void seed_fok_stp_book(OrderBook& book) {
  run(book, ask(1, 1, 1, 100, 2));
  run(book, ask(2, 2, 2, 100, 3));
  run(book, ask(3, 3, 1, 100, 5));
}

TEST(Fok, StopsAtOwnOrderUnderCancelNewestAndCancelBoth) {
  for (const StpMode mode : {StpMode::CancelNewest, StpMode::CancelBoth}) {
    OrderBook book;
    seed_fok_stp_book(book);
    const auto before = snapshot(book);
    // Only 2 is available before the walk reaches acct 2's own order.
    EXPECT_EQ(run(book, with_stp(fok(bid(4, 4, 2, 100, 6)), mode)),
              (std::vector<Event>{rej(4, 4, RejectReason::FokNotFillable)}));
    EXPECT_EQ(snapshot(book), before);
  }
}

TEST(Fok, FillableBeforeReachingOwnOrderFills) {
  OrderBook book;
  seed_fok_stp_book(book);
  const auto events = run(book, fok(bid(4, 4, 2, 100, 2)));
  ASSERT_EQ(events_of<Trade>(events).size(), 1u);
  EXPECT_TRUE(events_of<Cancelled>(events).empty());
  EXPECT_EQ(book.leaves_qty(2), std::optional<Qty>{3});  // own order untouched
}

TEST(Fok, SkipsOwnOrderUnderCancelOldestAndCancelsItWhenFilled) {
  OrderBook book;
  seed_fok_stp_book(book);
  const auto o = with_stp(fok(bid(4, 4, 2, 100, 6)), StpMode::CancelOldest);
  EXPECT_EQ(run(book, o), (std::vector<Event>{
      ack(o),
      trade(4, 1, 100, 2, 1, 4, B),
      maker_fill(4, 1, 1, 1, S, 100, 2, 0, 2),
      taker_fill(4, 1, 4, 2, B, 100, 2, 4, 2),
      cxl(4, 2, 3, kSelfTrade),
      trade(4, 2, 100, 4, 3, 4, B),
      maker_fill(4, 2, 3, 1, S, 100, 4, 1, 4),
      taker_fill(4, 2, 4, 2, B, 100, 4, 0, 6),
  }));
}

TEST(Fok, RejectedUnderCancelOldestLeavesOwnOrdersResting) {
  OrderBook book;
  seed_fok_stp_book(book);
  const auto before = snapshot(book);
  // 2 + 5 = 7 available from others; 8 is not fillable. Own order 2 must survive.
  EXPECT_EQ(run(book, with_stp(fok(bid(4, 4, 2, 100, 8)), StpMode::CancelOldest)),
            (std::vector<Event>{rej(4, 4, RejectReason::FokNotFillable)}));
  EXPECT_EQ(snapshot(book), before);
}

// --- Post-only --------------------------------------------------------------------

TEST(PostOnly, ThatDoesNotCrossRests) {
  OrderBook book;
  run(book, ask(1, 1, 1, 101, 5));
  const auto o = post_only(bid(2, 2, 2, 100, 4));
  EXPECT_EQ(run(book, o), (std::vector<Event>{ack(o)}));
  EXPECT_EQ(book.level_qty(B, 100), 4);
}

TEST(PostOnly, OnEmptyBookRests) {
  OrderBook book;
  const auto o = post_only(ask(1, 1, 1, 100, 4));
  EXPECT_EQ(run(book, o), (std::vector<Event>{ack(o)}));
  EXPECT_EQ(book.best_ask(), std::optional<Price>{100});
}

TEST(PostOnly, AtOrThroughOppositeBestIsRejectedWithoutRepricing) {
  OrderBook book;
  run(book, ask(1, 1, 1, 101, 5));
  run(book, bid(2, 2, 1, 99, 5));
  const auto before = snapshot(book);
  EXPECT_EQ(run(book, post_only(bid(3, 3, 2, 101, 4))),
            (std::vector<Event>{rej(3, 3, RejectReason::PostOnlyWouldCross)}));
  EXPECT_EQ(run(book, post_only(bid(4, 4, 2, 105, 4))),
            (std::vector<Event>{rej(4, 4, RejectReason::PostOnlyWouldCross)}));
  EXPECT_EQ(run(book, post_only(ask(5, 5, 2, 99, 4))),
            (std::vector<Event>{rej(5, 5, RejectReason::PostOnlyWouldCross)}));
  EXPECT_EQ(snapshot(book), before);
}

TEST(PostOnly, CrossingOwnAccountIsStillRejected) {
  OrderBook book;
  run(book, ask(1, 1, 7, 101, 5));
  EXPECT_EQ(run(book, post_only(bid(2, 2, 7, 101, 1))),
            (std::vector<Event>{rej(2, 2, RejectReason::PostOnlyWouldCross)}));
  EXPECT_EQ(book.leaves_qty(1), std::optional<Qty>{5});
}

TEST(PostOnly, MustBeGtcLimit) {
  OrderBook book;
  EXPECT_EQ(run(book, post_only(ioc(bid(1, 1, 1, 100, 1)))),
            (std::vector<Event>{rej(1, 1, RejectReason::InvalidOrderType)}));
  EXPECT_EQ(run(book, post_only(fok(bid(2, 2, 1, 100, 1)))),
            (std::vector<Event>{rej(2, 2, RejectReason::InvalidOrderType)}));
}

// --- Validation order -------------------------------------------------------------

TEST(Validation, PriceThenQtyThenTypeThenDuplicateThenTypeSpecific) {
  OrderBook book;
  auto m = market(1, 1, 1, B, 0);
  m.tif = TimeInForce::GTC;
  m.price = 5;
  EXPECT_EQ(run(book, m), (std::vector<Event>{rej(1, 1, RejectReason::InvalidPrice)}));
  m = market(2, 2, 1, B, 0);
  m.tif = TimeInForce::GTC;
  EXPECT_EQ(run(book, m), (std::vector<Event>{rej(2, 2, RejectReason::InvalidQty)}));

  run(book, bid(3, 10, 1, 100, 5));
  m = market(4, 10, 1, S, 5);
  m.tif = TimeInForce::GTC;
  EXPECT_EQ(run(book, m), (std::vector<Event>{rej(4, 10, RejectReason::InvalidOrderType)}));
  // Asks are empty, but the duplicate id is reported first.
  EXPECT_EQ(run(book, market(5, 10, 2, B, 1)),
            (std::vector<Event>{rej(5, 10, RejectReason::DuplicateOrderId)}));
  EXPECT_EQ(run(book, fok(ask(6, 10, 2, 100, 50))),
            (std::vector<Event>{rej(6, 10, RejectReason::DuplicateOrderId)}));
}

// --- Cancel-replace ---------------------------------------------------------------

// Three bids of 5 at 100 from accounts 1, 2, 3 (ids 1, 2, 3).
void seed_bid_queue(OrderBook& book) {
  run(book, bid(1, 1, 1, 100, 5));
  run(book, bid(2, 2, 2, 100, 5));
  run(book, bid(3, 3, 3, 100, 5));
}

TEST(Replace, QuantityDecreaseKeepsPriority) {
  OrderBook book;
  seed_bid_queue(book);
  EXPECT_EQ(run(book, replace(4, 1, 100, 3)),
            (std::vector<Event>{rpl(4, 1, 100, 3, 3, /*kept=*/true)}));
  EXPECT_EQ(book.level_orders(B, 100), (std::vector<OrderId>{1, 2, 3}));
  EXPECT_EQ(book.level_qty(B, 100), 13);
  EXPECT_EQ(book.leaves_qty(1), std::optional<Qty>{3});
}

TEST(Replace, SamePriceAndQuantityKeepsPriority) {
  OrderBook book;
  seed_bid_queue(book);
  EXPECT_EQ(run(book, replace(4, 1, 100, 5)),
            (std::vector<Event>{rpl(4, 1, 100, 5, 5, /*kept=*/true)}));
  EXPECT_EQ(book.level_orders(B, 100), (std::vector<OrderId>{1, 2, 3}));
}

TEST(Replace, QuantityIncreaseLosesPriority) {
  OrderBook book;
  seed_bid_queue(book);
  EXPECT_EQ(run(book, replace(4, 1, 100, 7)),
            (std::vector<Event>{rpl(4, 1, 100, 7, 7, /*kept=*/false)}));
  EXPECT_EQ(book.level_orders(B, 100), (std::vector<OrderId>{2, 3, 1}));
  EXPECT_EQ(book.level_qty(B, 100), 17);
}

TEST(Replace, PriceChangeLosesPriorityAndJoinsBackOfNewLevel) {
  OrderBook book;
  seed_bid_queue(book);
  run(book, bid(4, 4, 4, 99, 2));
  EXPECT_EQ(run(book, replace(5, 1, 99, 5)),
            (std::vector<Event>{rpl(5, 1, 99, 5, 5, /*kept=*/false)}));
  EXPECT_EQ(book.level_orders(B, 99), (std::vector<OrderId>{4, 1}));
  EXPECT_EQ(book.level_orders(B, 100), (std::vector<OrderId>{2, 3}));
  EXPECT_EQ(book.level_qty(B, 100), 10);
  EXPECT_EQ(book.level_qty(B, 99), 7);
}

TEST(Replace, PriceChangeEmptiesTheOldLevel) {
  OrderBook book;
  run(book, ask(1, 1, 1, 105, 5));
  run(book, replace(2, 1, 106, 5));
  EXPECT_EQ(book.level_count(S), 1u);
  EXPECT_EQ(book.best_ask(), std::optional<Price>{106});
}

TEST(Replace, PriceChangeThatCrossesMatchesAndRestsRemainder) {
  OrderBook book;
  run(book, ask(1, 1, 1, 102, 4));
  run(book, ask(2, 2, 2, 103, 3));
  run(book, bid(3, 3, 3, 100, 10));
  run(book, ask(4, 4, 4, 100, 2));  // order 3: filled 2, leaves 8

  EXPECT_EQ(run(book, replace(5, 3, 103, 12)), (std::vector<Event>{
      rpl(5, 3, 103, 12, 10, /*kept=*/false),
      trade(5, 2, 102, 4, 1, 3, B),
      maker_fill(5, 2, 1, 1, S, 102, 4, 0, 4),
      taker_fill(5, 2, 3, 3, B, 102, 4, 6, 6),
      trade(5, 3, 103, 3, 2, 3, B),
      maker_fill(5, 3, 2, 2, S, 103, 3, 0, 3),
      taker_fill(5, 3, 3, 3, B, 103, 3, 3, 9),
  }));
  EXPECT_EQ(book.best_bid(), std::optional<Price>{103});
  EXPECT_EQ(book.leaves_qty(3), std::optional<Qty>{3});
  EXPECT_EQ(book.best_ask(), std::nullopt);
}

TEST(Replace, PriceChangeThatFullyFillsRemovesTheOrder) {
  OrderBook book;
  run(book, ask(1, 1, 1, 102, 4));
  run(book, bid(2, 2, 2, 100, 4));
  const auto events = run(book, replace(3, 2, 102, 4));
  ASSERT_EQ(events_of<Fill>(events).size(), 2u);
  EXPECT_EQ(events_of<Fill>(events)[1].leaves_qty, 0);
  EXPECT_EQ(book.order_count(), 0u);
}

TEST(Replace, ToOrBelowFilledQuantityIsRejected) {
  OrderBook book;
  run(book, bid(1, 1, 1, 100, 10));
  run(book, ask(2, 2, 2, 100, 4));  // order 1: filled 4, leaves 6
  const auto before = snapshot(book);
  EXPECT_EQ(run(book, replace(3, 1, 100, 4)),
            (std::vector<Event>{rej(3, 1, RejectReason::ReplaceQtyTooLow)}));
  EXPECT_EQ(run(book, replace(4, 1, 101, 3)),
            (std::vector<Event>{rej(4, 1, RejectReason::ReplaceQtyTooLow)}));
  EXPECT_EQ(snapshot(book), before);
  // Just above filled is accepted: leaves 1, priority kept.
  EXPECT_EQ(run(book, replace(5, 1, 100, 5)),
            (std::vector<Event>{rpl(5, 1, 100, 5, 1, /*kept=*/true)}));
}

TEST(Replace, UnknownOrderIsRejected) {
  OrderBook book;
  EXPECT_EQ(run(book, replace(1, 9, 100, 5)),
            (std::vector<Event>{rej(1, 9, RejectReason::UnknownOrder)}));
  run(book, bid(2, 1, 1, 100, 5));
  run(book, cancel(3, 1));
  EXPECT_EQ(run(book, replace(4, 1, 100, 5)),
            (std::vector<Event>{rej(4, 1, RejectReason::UnknownOrder)}));
}

TEST(Replace, FieldChecksComeBeforeTheOrderLookup) {
  OrderBook book;
  EXPECT_EQ(run(book, replace(1, 9, 0, 5)),
            (std::vector<Event>{rej(1, 9, RejectReason::InvalidPrice)}));
  EXPECT_EQ(run(book, replace(2, 9, 100, 0)),
            (std::vector<Event>{rej(2, 9, RejectReason::InvalidQty)}));
}

TEST(Replace, PostOnlyToCrossingPriceIsRejectedAndOriginalKept) {
  OrderBook book;
  run(book, ask(1, 1, 1, 105, 5));
  run(book, post_only(bid(2, 2, 2, 101, 2)));
  const auto before = snapshot(book);
  EXPECT_EQ(run(book, replace(3, 2, 105, 2)),
            (std::vector<Event>{rej(3, 2, RejectReason::PostOnlyWouldCross)}));
  EXPECT_EQ(snapshot(book), before);
  // A non-crossing re-price of a post-only order is fine.
  EXPECT_EQ(run(book, replace(4, 2, 104, 2)),
            (std::vector<Event>{rpl(4, 2, 104, 2, 2, /*kept=*/false)}));
}

TEST(Replace, PriceChangeUsesTheOrdersStoredStpMode) {
  OrderBook book;
  run(book, ask(1, 1, 7, 101, 5));
  run(book, with_stp(bid(2, 2, 7, 100, 3), StpMode::CancelNewest));
  // Re-pricing onto its own ask cancels the replaced order itself.
  EXPECT_EQ(run(book, replace(3, 2, 101, 3)), (std::vector<Event>{
      rpl(3, 2, 101, 3, 3, /*kept=*/false),
      cxl(3, 2, 3, kSelfTrade),
  }));
  EXPECT_EQ(book.leaves_qty(2), std::nullopt);
  EXPECT_EQ(book.leaves_qty(1), std::optional<Qty>{5});

  run(book, with_stp(bid(4, 3, 7, 100, 3), StpMode::CancelOldest));
  EXPECT_EQ(run(book, replace(5, 3, 101, 3)), (std::vector<Event>{
      rpl(5, 3, 101, 3, 3, /*kept=*/false),
      cxl(5, 1, 5, kSelfTrade),
  }));
  EXPECT_EQ(book.best_bid(), std::optional<Price>{101});  // rests after cancelling the ask
}

// --- Self-trade prevention --------------------------------------------------------

TEST(Stp, DefaultModeIsCancelNewest) {
  EXPECT_EQ(NewOrder{}.stp, StpMode::CancelNewest);
}

TEST(Stp, CancelNewestAtTopOfBookCancelsIncomingOnly) {
  OrderBook book;
  run(book, ask(1, 1, 7, 100, 5));
  const auto o = bid(2, 2, 7, 100, 3);
  EXPECT_EQ(run(book, o), (std::vector<Event>{ack(o), cxl(2, 2, 3, kSelfTrade)}));
  EXPECT_EQ(book.leaves_qty(1), std::optional<Qty>{5});
  EXPECT_EQ(book.best_bid(), std::nullopt);  // a GTC cancelled by STP does not rest
}

TEST(Stp, CancelNewestMidSweepKeepsEarlierFills) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 3));
  run(book, ask(2, 2, 2, 101, 2));
  run(book, ask(3, 3, 3, 101, 4));
  run(book, ask(4, 4, 1, 102, 5));
  const auto o = bid(5, 5, 3, 102, 10);
  EXPECT_EQ(run(book, o), (std::vector<Event>{
      ack(o),
      trade(5, 1, 100, 3, 1, 5, B),
      maker_fill(5, 1, 1, 1, S, 100, 3, 0, 3),
      taker_fill(5, 1, 5, 3, B, 100, 3, 7, 3),
      trade(5, 2, 101, 2, 2, 5, B),
      maker_fill(5, 2, 2, 2, S, 101, 2, 0, 2),
      taker_fill(5, 2, 5, 3, B, 101, 2, 5, 5),
      cxl(5, 5, 5, kSelfTrade),
  }));
  EXPECT_EQ(book.level_orders(S, 101), (std::vector<OrderId>{3}));
  EXPECT_EQ(book.level_qty(S, 102), 5);
}

TEST(Stp, CancelOldestCancelsRestingAcrossLevelsAndContinues) {
  OrderBook book;
  run(book, ask(1, 1, 2, 100, 2));
  run(book, ask(2, 2, 1, 100, 3));
  run(book, ask(3, 3, 2, 101, 4));
  run(book, ask(4, 4, 1, 101, 1));
  run(book, ask(5, 5, 1, 103, 6));
  run(book, ask(6, 6, 2, 103, 1));  // own, beyond the limit
  const auto o = with_stp(bid(7, 7, 2, 101, 6), StpMode::CancelOldest);
  EXPECT_EQ(run(book, o), (std::vector<Event>{
      ack(o),
      cxl(7, 1, 2, kSelfTrade),
      trade(7, 1, 100, 3, 2, 7, B),
      maker_fill(7, 1, 2, 1, S, 100, 3, 0, 3),
      taker_fill(7, 1, 7, 2, B, 100, 3, 3, 3),
      cxl(7, 3, 4, kSelfTrade),
      trade(7, 2, 101, 1, 4, 7, B),
      maker_fill(7, 2, 4, 1, S, 101, 1, 0, 1),
      taker_fill(7, 2, 7, 2, B, 101, 1, 2, 4),
  }));
  EXPECT_EQ(book.leaves_qty(7), std::optional<Qty>{2});  // remainder rests
  EXPECT_EQ(book.level_orders(S, 103), (std::vector<OrderId>{5, 6}));
}

TEST(Stp, CancelOldestOnIocCancelsRemainderAsUnfilled) {
  OrderBook book;
  run(book, ask(1, 1, 7, 100, 5));
  const auto o = with_stp(ioc(bid(2, 2, 7, 100, 3)), StpMode::CancelOldest);
  EXPECT_EQ(run(book, o), (std::vector<Event>{
      ack(o),
      cxl(2, 1, 5, kSelfTrade),
      cxl(2, 2, 3, kUnfilled),
  }));
  EXPECT_EQ(book.order_count(), 0u);
}

TEST(Stp, CancelBothMidSweepOnMarketOrder) {
  OrderBook book;
  run(book, bid(1, 1, 1, 100, 4));
  run(book, bid(2, 2, 2, 100, 5));
  run(book, bid(3, 3, 1, 99, 5));
  const auto m = with_stp(market(4, 4, 2, S, 8), StpMode::CancelBoth);
  EXPECT_EQ(run(book, m), (std::vector<Event>{
      ack(m),
      trade(4, 1, 100, 4, 1, 4, S),
      maker_fill(4, 1, 1, 1, B, 100, 4, 0, 4),
      taker_fill(4, 1, 4, 2, S, 100, 4, 4, 4),
      cxl(4, 2, 5, kSelfTrade),
      cxl(4, 4, 4, kSelfTrade),
  }));
  EXPECT_EQ(book.level_count(B), 1u);
  EXPECT_EQ(book.level_qty(B, 99), 5);
}

TEST(Stp, MarketWhoseOnlyLiquidityIsOwnIsAcceptedThenCancelled) {
  OrderBook book;
  run(book, bid(1, 1, 7, 99, 5));
  const auto m = market(2, 2, 7, S, 2);
  EXPECT_EQ(run(book, m), (std::vector<Event>{ack(m), cxl(2, 2, 2, kSelfTrade)}));
  EXPECT_EQ(book.leaves_qty(1), std::optional<Qty>{5});
}

TEST(Stp, OwnOrdersBeyondTheLimitAreNotTouched) {
  OrderBook book;
  run(book, ask(1, 1, 1, 100, 2));
  run(book, ask(2, 2, 7, 101, 2));
  const auto o = with_stp(bid(3, 3, 7, 100, 5), StpMode::CancelBoth);
  const auto events = run(book, o);
  EXPECT_TRUE(events_of<Cancelled>(events).empty());
  EXPECT_EQ(book.leaves_qty(2), std::optional<Qty>{2});
  EXPECT_EQ(book.leaves_qty(3), std::optional<Qty>{3});
}

TEST(Cancel, UserCancelHasUserReason) {
  OrderBook book;
  run(book, bid(1, 1, 1, 100, 5));
  EXPECT_EQ(run(book, cancel(2, 1)),
            (std::vector<Event>{cxl(2, 1, 5, CancelReason::User)}));
}

}  // namespace
}  // namespace mf
