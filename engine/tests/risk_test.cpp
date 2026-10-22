#include <gtest/gtest.h>

#include <cstdint>
#include <functional>
#include <limits>
#include <random>
#include <string>
#include <vector>

#include "mf/engine.hpp"
#include "risk_fixture.hpp"
#include "test_helpers.hpp"

// Pre-trade risk gate (M5, docs/risk.md): one test per reject code, the check order,
// market-order protection, exposure kept from events, and runtime admin changes.

namespace mf {
namespace {

using test::acked;
using test::events_of;
using test::i64;
using test::rejected;
using test::RiskFixture;

constexpr Side B = Side::Buy;
constexpr Side S = Side::Sell;
constexpr std::int64_t kMax = std::numeric_limits<std::int64_t>::max();

class Risk : public RiskFixture, public ::testing::Test {};

// --- One test per reject code ---------------------------------------------------------

TEST_F(Risk, MaxQtyAllowsTheLimitAndRejectsOneMore) {
  set_instrument({.max_qty = 100});
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 100)));
  EXPECT_TRUE(rejected(limit(2, 1, B, 100, 101), RejectReason::MaxQty));
  EXPECT_TRUE(rejected(market(3, 1, S, 101), RejectReason::MaxQty));
  EXPECT_TRUE(rejected(replace(1, 100, 101), RejectReason::MaxQty));
}

TEST_F(Risk, MaxNotionalAllowsTheLimitAndRejectsOneMore) {
  set_account(1, {.max_notional = 10'000});
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 100)));
  EXPECT_TRUE(rejected(limit(2, 1, B, 100, 101), RejectReason::MaxNotional));
  EXPECT_TRUE(rejected(limit(3, 1, S, 10'001, 1), RejectReason::MaxNotional));
  EXPECT_TRUE(acked(limit(4, 2, S, 200, 101)));  // account 2 has no limits
}

TEST_F(Risk, DefaultLimitsApplyToAccountsWithoutTheirOwn) {
  set_account(std::nullopt, {.max_notional = 1'000});
  set_account(3, {.max_notional = 5'000});
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 11), RejectReason::MaxNotional));
  EXPECT_TRUE(rejected(limit(2, 2, B, 100, 11), RejectReason::MaxNotional));
  EXPECT_TRUE(acked(limit(3, 3, B, 100, 50)));  // own limits replace the defaults
}

TEST_F(Risk, PriceBandAroundTheLastTrade) {
  set_instrument({.band_bps = 500});  // 5%
  trade_at(100);
  EXPECT_TRUE(rejected(limit(10, 1, B, 106, 1), RejectReason::PriceBand));
  EXPECT_TRUE(acked(limit(11, 1, B, 105, 1)));  // exactly 5% away
  cancel(11);
  EXPECT_TRUE(acked(limit(12, 4, S, 95, 1)));
  EXPECT_TRUE(rejected(limit(13, 4, S, 94, 1), RejectReason::PriceBand));
  EXPECT_TRUE(rejected(replace(12, 94, 1), RejectReason::PriceBand));
}

TEST_F(Risk, PriceBandAroundTheMidWhenThereIsNoTrade) {
  limit(1, 2, B, 99, 1);
  limit(2, 3, S, 102, 1);  // mid 100.5, compared as 2 * px against 201
  set_instrument({.band_bps = 100});  // 1%: 2px within 201 +- 2.01
  EXPECT_TRUE(rejected(limit(3, 1, B, 102, 1), RejectReason::PriceBand));  // |204 - 201| = 3
  EXPECT_TRUE(rejected(limit(4, 1, S, 99, 1), RejectReason::PriceBand));   // |198 - 201| = 3
  EXPECT_TRUE(acked(limit(5, 1, B, 101, 1)));   // |202 - 201| = 1
}

TEST_F(Risk, LastTradeTakesPrecedenceOverTheMid) {
  trade_at(100);
  limit(10, 2, B, 118, 1);
  limit(11, 3, S, 122, 1);  // mid 120, last trade 100
  set_instrument({.band_bps = 500});
  EXPECT_TRUE(acked(limit(12, 1, B, 104, 1)));
  EXPECT_TRUE(rejected(limit(13, 1, B, 119, 1), RejectReason::PriceBand));
}

TEST_F(Risk, PriceBandFallsBackToTheConfiguredReference) {
  limit(1, 2, S, 150, 1);  // one-sided book, no trade: no mid
  set_instrument({.band_bps = 1'000, .reference_price = 100});
  EXPECT_TRUE(rejected(limit(2, 1, B, 111, 1), RejectReason::PriceBand));
  EXPECT_TRUE(acked(limit(3, 1, B, 110, 1)));
}

TEST_F(Risk, NoReferenceSkipsTheBandForLimitOrders) {
  set_instrument({.band_bps = 100});
  EXPECT_TRUE(acked(limit(1, 1, B, 1, 1)));
  EXPECT_TRUE(acked(limit(2, 2, S, 1'000'000, 1)));
}

TEST_F(Risk, PositionLimitCountsOpenOrdersOnEachSide) {
  set_account(1, {.position_limit = 10});
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 6)));
  EXPECT_TRUE(acked(limit(2, 1, B, 99, 4)));    // worst case +10
  EXPECT_TRUE(rejected(limit(3, 1, B, 98, 1), RejectReason::PositionLimit));
  EXPECT_TRUE(acked(limit(4, 1, S, 110, 10)));  // sells are counted separately: -10
  EXPECT_TRUE(rejected(limit(5, 1, S, 111, 1), RejectReason::PositionLimit));
}

TEST_F(Risk, PositionLimitCountsFilledPosition) {
  set_account(1, {.position_limit = 10});
  limit(1, 2, S, 100, 10);
  limit(2, 1, B, 100, 10);  // fills: position +10
  EXPECT_EQ(engine.risk().position(1), 10);
  EXPECT_TRUE(rejected(limit(3, 1, B, 100, 1), RejectReason::PositionLimit));
  EXPECT_TRUE(acked(limit(4, 1, S, 110, 20)));  // to -10 at worst
  EXPECT_TRUE(rejected(limit(5, 1, S, 110, 1), RejectReason::PositionLimit));
}

TEST_F(Risk, CreditCoversOpenOrders) {
  set_account(1, {.credit = 1'000});
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 5)));   // 500
  EXPECT_TRUE(acked(limit(2, 1, S, 110, 4)));   // 440: both sides count
  EXPECT_TRUE(rejected(limit(3, 1, B, 100, 1), RejectReason::InsufficientCredit));
  EXPECT_TRUE(acked(limit(4, 1, B, 60, 1)));    // exactly 1000
  cancel(1);                                     // frees 500
  EXPECT_TRUE(acked(limit(5, 1, B, 100, 5)));
}

// Credit covers open orders only: once an order fills, its notional leaves the credit
// check and the position limit bounds what is held (docs/risk.md).
TEST_F(Risk, FilledQuantityFreesCredit) {
  set_account(1, {.credit = 1'000});
  limit(1, 1, B, 100, 10);
  EXPECT_TRUE(rejected(limit(2, 1, B, 100, 1), RejectReason::InsufficientCredit));
  limit(3, 2, S, 100, 10);  // fills order 1
  EXPECT_EQ(i64(engine.risk().exposure(1).open_notional), 0);
  EXPECT_TRUE(acked(limit(4, 1, B, 100, 10)));
}

TEST_F(Risk, MaxOpenOrdersCountsOnlyOrdersThatCanRest) {
  set_account(1, {.max_open_orders = 2});
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 1)));
  EXPECT_TRUE(acked(limit(2, 1, B, 99, 1, TimeInForce::Day)));
  EXPECT_TRUE(rejected(limit(3, 1, B, 98, 1), RejectReason::MaxOpenOrders));
  EXPECT_TRUE(rejected(limit(4, 1, S, 120, 1, TimeInForce::Day), RejectReason::MaxOpenOrders));
  EXPECT_TRUE(acked(limit(5, 1, S, 120, 1, TimeInForce::IOC)));  // never rests
  limit(6, 2, S, 130, 1);
  EXPECT_TRUE(acked(market(7, 1, B, 1)));
  cancel(1);
  EXPECT_TRUE(acked(limit(8, 1, B, 98, 1)));
}

TEST_F(Risk, RateLimitAllowsABurstThenRefillsOverTime) {
  set_account(1, {.rate = RateLimit{.per_sec = 2, .burst = 3}});
  at(0);
  for (OrderId id = 1; id <= 3; ++id) EXPECT_TRUE(acked(limit(id, 1, B, 100, 1))) << id;
  EXPECT_TRUE(rejected(limit(4, 1, B, 100, 1), RejectReason::RateLimit));
  EXPECT_TRUE(rejected(replace(1, 100, 2), RejectReason::RateLimit));
  EXPECT_TRUE(acked(cancel(1)));                // cancels are never throttled
  EXPECT_TRUE(acked(limit(5, 2, B, 100, 1)));   // other accounts have their own bucket

  at(499'999'999);  // 2/s for just under 0.5 s: 0.999999998 tokens
  EXPECT_TRUE(rejected(limit(6, 1, B, 100, 1), RejectReason::RateLimit));
  at(500'000'000);  // exactly one token
  EXPECT_TRUE(acked(limit(7, 1, B, 100, 1)));
  EXPECT_TRUE(rejected(limit(8, 1, B, 100, 1), RejectReason::RateLimit));
}

TEST_F(Risk, RateLimitChargesOrdersThatFailLaterChecks) {
  set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 2}});
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 0), RejectReason::InvalidQty));
  EXPECT_TRUE(acked(limit(2, 1, B, 100, 1)));
  EXPECT_TRUE(rejected(limit(3, 1, B, 100, 1), RejectReason::RateLimit));
}

TEST_F(Risk, RateLimitRefillIsCappedAtTheBurstAfterAHugeGap) {
  set_account(1, {.rate = RateLimit{.per_sec = kMaxRate, .burst = 2}});
  at(0);
  limit(1, 1, B, 100, 1);
  limit(2, 1, B, 100, 1);
  at(kMax / 2);  // elapsed * rate overflows int64; must not wrap
  EXPECT_TRUE(acked(limit(3, 1, B, 100, 1)));
  EXPECT_TRUE(acked(limit(4, 1, B, 100, 1)));
  EXPECT_TRUE(rejected(limit(5, 1, B, 100, 1), RejectReason::RateLimit));
}

TEST_F(Risk, LoweringTheBurstClampsTheTokens) {
  set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 5}});
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 1)));  // bucket now 4
  set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 1}});
  EXPECT_TRUE(acked(limit(2, 1, B, 100, 1)));
  EXPECT_TRUE(rejected(limit(3, 1, B, 100, 1), RejectReason::RateLimit));
}

TEST_F(Risk, TimestampGoingBackwardsIsAnInvariantViolation) {
  set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 5}});
  at(1'000);
  limit(1, 1, B, 100, 1);
  at(999);
  EXPECT_THROW(limit(2, 1, B, 100, 1), InvariantViolation);
}

TEST_F(Risk, DuplicateOrderIdIsCheckedBeforeRiskLimits) {
  set_instrument({.max_qty = 10});
  limit(1, 1, B, 100, 5);
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 50), RejectReason::DuplicateOrderId));
}

TEST_F(Risk, AccountKillSwitchBlocksNewOrdersAndReplacesButNotCancels) {
  limit(1, 1, B, 100, 5);
  limit(2, 1, B, 99, 5);
  kill(1, true);
  EXPECT_TRUE(rejected(limit(3, 1, B, 98, 1), RejectReason::KillSwitch));
  EXPECT_TRUE(rejected(replace(1, 100, 4), RejectReason::KillSwitch));
  EXPECT_TRUE(acked(cancel(1)));
  EXPECT_TRUE(acked(limit(4, 2, S, 120, 1)));
  kill(1, false);
  EXPECT_TRUE(acked(limit(5, 1, B, 98, 1)));
}

TEST_F(Risk, GlobalKillSwitchBlocksEveryAccount) {
  kill(std::nullopt, true);
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 1), RejectReason::KillSwitch));
  EXPECT_TRUE(rejected(limit(2, 2, S, 100, 1), RejectReason::KillSwitch));
  kill(std::nullopt, false);
  EXPECT_TRUE(acked(limit(3, 1, B, 100, 1)));
}

TEST_F(Risk, KillSwitchCancelOpenCancelsInBookOrder) {
  limit(1, 1, B, 99, 5);
  limit(2, 2, B, 100, 3);
  limit(3, 1, B, 100, 4);
  limit(4, 1, S, 105, 2);
  limit(5, 2, S, 104, 1);
  const auto ev = kill(1, true, true);
  const SeqNo s = seq;
  EXPECT_EQ(ev, (std::vector<Event>{
                    AdminAck{.seq_no = s, .kind = AdminKind::KillSwitch},
                    Cancelled{.seq_no = s, .order_id = 3, .cancelled_qty = 4,
                              .reason = CancelReason::KillSwitch},
                    Cancelled{.seq_no = s, .order_id = 1, .cancelled_qty = 5,
                              .reason = CancelReason::KillSwitch},
                    Cancelled{.seq_no = s, .order_id = 4, .cancelled_qty = 2,
                              .reason = CancelReason::KillSwitch},
                }));
  EXPECT_EQ(engine.risk().exposure(1), RiskGate::Exposure{});
  EXPECT_EQ(engine.book().order_count(), 2u);
}

TEST_F(Risk, GlobalKillSwitchCancelOpenCancelsEverything) {
  limit(1, 1, B, 99, 5);
  limit(2, 2, S, 105, 3);
  const auto ev = kill(std::nullopt, true, true);
  EXPECT_EQ(events_of<Cancelled>(ev).size(), 2u);
  EXPECT_EQ(engine.book().order_count(), 0u);
}

TEST_F(Risk, CancelOpenWithoutEngagingIsRefused) {
  limit(1, 1, B, 99, 5);
  const auto ev = send(KillSwitch{.account_id = 1, .engaged = false, .cancel_open = true});
  EXPECT_EQ(ev, (std::vector<Event>{AdminReject{
                    .seq_no = seq, .reason = AdminRejectReason::InvalidKillSwitch}}));
  EXPECT_EQ(engine.book().order_count(), 1u);
}

// --- Check order ------------------------------------------------------------------------

// Each case fails two adjacent checks; the earlier one is reported (docs/risk.md).
TEST(RiskCheckOrder, ReportsTheFirstFailingCheck) {
  struct Case {
    const char* name;
    std::function<void(RiskFixture&)> setup;
    NewOrder order;
    RejectReason expected;
  };
  const NewOrder buy{.order_id = 100, .account_id = 1, .side = B, .price = 100, .qty = 10};
  auto with = [&](auto f) {
    NewOrder o = buy;
    f(o);
    return o;
  };
  const std::vector<Case> cases = {
      {"session_before_kill",
       [](RiskFixture& f) {
         f.kill(1, true);
         f.session(SessionState::Halted);
       },
       buy, RejectReason::MarketClosed},
      {"kill_before_rate",
       [](RiskFixture& f) {
         f.set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 1}});
         f.limit(1, 1, B, 90, 1);
         f.kill(1, true);
       },
       buy, RejectReason::KillSwitch},
      {"rate_before_price",
       [](RiskFixture& f) {
         f.set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 1}});
         f.limit(1, 1, B, 90, 1);
       },
       with([](NewOrder& o) { o.price = 0; }), RejectReason::RateLimit},
      {"duplicate_before_max_qty",
       [](RiskFixture& f) {
         f.set_instrument({.max_qty = 5});
         f.limit(100, 2, S, 120, 1);
       },
       buy, RejectReason::DuplicateOrderId},
      {"max_qty_before_band",
       [](RiskFixture& f) {
         f.set_instrument({.max_qty = 5, .band_bps = 100, .reference_price = 50});
       },
       buy, RejectReason::MaxQty},
      {"band_before_notional",
       [](RiskFixture& f) {
         f.set_instrument({.band_bps = 100, .reference_price = 50});
         f.set_account(1, {.max_notional = 10});
       },
       buy, RejectReason::PriceBand},
      {"notional_before_open_orders",
       [](RiskFixture& f) {
         f.set_account(1, {.max_notional = 10, .max_open_orders = 0});
       },
       buy, RejectReason::MaxNotional},
      {"open_orders_before_position",
       [](RiskFixture& f) { f.set_account(1, {.position_limit = 1, .max_open_orders = 0}); },
       buy, RejectReason::MaxOpenOrders},
      {"position_before_credit",
       [](RiskFixture& f) { f.set_account(1, {.position_limit = 1, .credit = 10}); },
       buy, RejectReason::PositionLimit},
      {"credit_before_fok",
       [](RiskFixture& f) { f.set_account(1, {.credit = 10}); },
       with([](NewOrder& o) { o.tif = TimeInForce::FOK; }), RejectReason::InsufficientCredit},
      {"credit_before_post_only",
       [](RiskFixture& f) {
         f.limit(1, 2, S, 100, 1);
         f.set_account(1, {.credit = 10});
       },
       with([](NewOrder& o) { o.post_only = true; }), RejectReason::InsufficientCredit},
  };
  for (const auto& c : cases) {
    RiskFixture f;
    c.setup(f);
    EXPECT_TRUE(rejected(f.send(c.order), c.expected)) << c.name;
  }
}

TEST(RiskCheckOrder, ReplaceChecksBookStateBeforeKillAndRate) {
  RiskFixture f;
  f.limit(1, 1, B, 100, 10);
  f.limit(2, 2, S, 100, 4);  // order 1 has filled 4
  f.kill(1, true);
  EXPECT_TRUE(rejected(f.replace(9, 100, 5), RejectReason::UnknownOrder));
  EXPECT_TRUE(rejected(f.replace(1, 100, 4), RejectReason::ReplaceQtyTooLow));
  EXPECT_TRUE(rejected(f.replace(1, 100, 5), RejectReason::KillSwitch));
}

// A risk reject is the command's only event and changes nothing but the seq_no (and,
// past the rate check, one rate token).
TEST_F(Risk, RejectsLeaveNoTrace) {
  set_account(1, {.max_notional = 1'000, .position_limit = 50});
  limit(1, 1, B, 90, 5);
  limit(2, 2, S, 110, 5);
  EngineState before = engine.state();
  EXPECT_TRUE(rejected(limit(3, 1, B, 100, 11), RejectReason::MaxNotional));
  EngineState after = engine.state();
  before.book.last_seq_no = after.book.last_seq_no;
  EXPECT_EQ(after, before);
  EXPECT_EQ(engine.risk().exposure(1).open_orders, 1);
}

// --- Market orders ----------------------------------------------------------------------

TEST_F(Risk, MarketBuyTradesOnlyUpToTheBandEdge) {
  trade_at(100);
  limit(10, 2, S, 100, 2);
  limit(11, 2, S, 103, 2);
  limit(12, 2, S, 110, 5);
  set_instrument({.band_bps = 500});  // edge 105
  const auto ev = market(13, 1, B, 10);
  const auto trades = events_of<Trade>(ev);
  ASSERT_EQ(trades.size(), 2u);
  EXPECT_EQ(trades[0].price, 100);
  EXPECT_EQ(trades[1].price, 103);
  EXPECT_EQ(std::get<Cancelled>(ev.back()),
            (Cancelled{.seq_no = seq, .order_id = 13, .cancelled_qty = 6,
                       .reason = CancelReason::Unfilled}));
  EXPECT_EQ(engine.book().best_ask(), 110);
}

TEST_F(Risk, MarketSellTradesOnlyDownToTheBandEdge) {
  trade_at(100);
  limit(10, 2, B, 100, 2);
  limit(11, 2, B, 96, 2);
  limit(12, 2, B, 94, 5);
  set_instrument({.band_bps = 500});  // edge 95
  const auto ev = market(13, 1, S, 10);
  EXPECT_EQ(events_of<Trade>(ev).size(), 2u);
  EXPECT_EQ(events_of<Cancelled>(ev).back().cancelled_qty, 6);
  EXPECT_EQ(engine.book().best_bid(), 94);
}

TEST_F(Risk, MarketOrderWithNothingInsideTheBandIsAckedAndCancelled) {
  trade_at(100);
  limit(10, 2, S, 120, 5);
  set_instrument({.band_bps = 500});
  const auto ev = market(11, 1, B, 3);
  ASSERT_EQ(ev.size(), 2u);
  EXPECT_TRUE(std::holds_alternative<Ack>(ev[0]));
  EXPECT_EQ(std::get<Cancelled>(ev[1]).cancelled_qty, 3);
}

TEST_F(Risk, MarketOrderWithABandButNoReferenceIsRejected) {
  limit(1, 2, S, 100, 5);  // one-sided, no trade, no configured reference
  set_instrument({.band_bps = 500});
  EXPECT_TRUE(rejected(market(2, 1, B, 1), RejectReason::PriceBand));
}

TEST_F(Risk, MarketOrderIsNeverRejectedForNoReferenceWithoutValueLimits) {
  limit(1, 2, S, 100, 5);
  set_instrument({.max_qty = 10});
  set_account(1, {.position_limit = 100, .max_open_orders = 0});
  EXPECT_TRUE(acked(market(2, 1, B, 5)));
}

TEST_F(Risk, MarketOrderIsValuedByWalkingTheBookWithoutABand) {
  set_account(1, {.max_notional = 500});
  limit(1, 2, S, 100, 2);
  limit(2, 2, S, 110, 3);
  EXPECT_TRUE(rejected(market(3, 1, B, 5), RejectReason::MaxNotional));  // 200 + 330
  EXPECT_TRUE(acked(market(4, 1, B, 4)));                                // 200 + 220
  EXPECT_EQ(engine.book().level_qty(S, 110), 1);
}

TEST_F(Risk, MarketWalkCountsOnlyAvailableQuantity) {
  set_account(1, {.max_notional = 250});
  limit(1, 2, S, 100, 2);
  EXPECT_TRUE(acked(market(2, 1, B, 10)));  // can trade at most 200
}

TEST_F(Risk, MarketWalkSkipsTheAccountsOwnOrders) {
  set_account(1, {.max_notional = 550});
  limit(1, 1, S, 100, 5);  // own: STP could cancel it and sweep on
  limit(2, 2, S, 120, 5);
  EXPECT_TRUE(rejected(market(3, 1, B, 5), RejectReason::MaxNotional));  // 600
}

TEST_F(Risk, MarketWalkStopsAtTheBandEdge) {
  trade_at(100);
  limit(10, 2, S, 104, 2);
  limit(11, 2, S, 200, 10);
  set_instrument({.band_bps = 500});
  set_account(1, {.max_notional = 300});
  EXPECT_TRUE(acked(market(12, 1, B, 10)));  // only 2 x 104 is reachable
}

TEST_F(Risk, MarketOrderCreditUsesTheWalk) {
  set_account(1, {.credit = 500});
  limit(1, 2, S, 100, 2);
  limit(2, 2, S, 110, 3);
  EXPECT_TRUE(rejected(market(3, 1, B, 5), RejectReason::InsufficientCredit));
}

// --- Overflow -----------------------------------------------------------------------------

TEST_F(Risk, HugeNotionalsAreRejectedNotWrapped) {
  set_account(1, {.max_notional = kMax, .position_limit = kMax, .credit = kMax});
  EXPECT_TRUE(rejected(limit(1, 1, B, kMax / 2, 4), RejectReason::MaxNotional));
  set_account(1, {.position_limit = kMax, .credit = kMax});
  EXPECT_TRUE(rejected(limit(2, 1, B, 3'000'000'000'000'000'000, 4),
                       RejectReason::InsufficientCredit));
  set_account(1, {.position_limit = kMax});
  EXPECT_TRUE(acked(limit(3, 1, B, 1, kMax)));
  EXPECT_TRUE(rejected(limit(4, 1, B, 1, kMax), RejectReason::PositionLimit));
}

// --- Exposure and positions from events ----------------------------------------------------

TEST_F(Risk, ExposureAndPositionFollowFillsCancelsAndReplaces) {
  using E = RiskGate::Exposure;
  limit(1, 1, B, 100, 10);
  limit(2, 1, B, 99, 5);
  EXPECT_EQ(engine.risk().exposure(1),
            (E{.open_notional = 1'495, .open_buy_qty = 15, .open_orders = 2}));

  limit(3, 2, S, 99, 12);  // fills 10 @ 100 and 2 @ 99
  EXPECT_EQ(engine.risk().position(1), 12);
  EXPECT_EQ(engine.risk().position(2), -12);
  EXPECT_EQ(engine.risk().exposure(1), (E{.open_notional = 297, .open_buy_qty = 3, .open_orders = 1}));
  EXPECT_EQ(engine.risk().exposure(2), E{});

  replace(2, 99, 8);  // total 8, filled 2: leaves 6
  EXPECT_EQ(engine.risk().exposure(1), (E{.open_notional = 594, .open_buy_qty = 6, .open_orders = 1}));
  replace(2, 98, 4);  // leaves 2 at 98
  EXPECT_EQ(engine.risk().exposure(1), (E{.open_notional = 196, .open_buy_qty = 2, .open_orders = 1}));
  cancel(2);
  EXPECT_EQ(engine.risk().exposure(1), E{});

  limit(4, 1, B, 100, 5, TimeInForce::IOC);  // nothing to fill: acked and cancelled
  EXPECT_EQ(engine.risk().exposure(1), E{});

  limit(5, 1, S, 105, 4);
  send(NewOrder{.order_id = 6, .account_id = 1, .side = B, .stp = StpMode::CancelOldest,
                .price = 105, .qty = 1});  // STP cancels order 5; order 6 rests
  EXPECT_EQ(engine.risk().exposure(1), (E{.open_notional = 105, .open_buy_qty = 1, .open_orders = 1}));
  EXPECT_EQ(engine.state().risk.positions,
            (std::vector<RiskState::Position>{{.account_id = 1, .qty = 12},
                                              {.account_id = 2, .qty = -12}}));
}

// Exposure kept incrementally equals exposure rebuilt from the book, after every
// command of a random stream with limits, kill switches and sessions.
TEST(RiskConsistency, ExposureMatchesTheBookOnARandomStream) {
  RiskFixture f;
  f.set_account(std::nullopt, {.position_limit = 40, .credit = 20'000, .max_open_orders = 8});
  std::mt19937_64 rng(5);
  for (int i = 0; i < 4'000; ++i) {
    const auto r = rng() % 100;
    const OrderId id = 1 + rng() % 400;
    const AccountId acct = 1 + rng() % 4;
    const Side side = rng() % 2 ? B : S;
    const Price px = 95 + static_cast<Price>(rng() % 11);
    const Qty qty = 1 + static_cast<Qty>(rng() % 15);
    if (r < 15) {
      f.cancel(id);
    } else if (r < 25) {
      f.replace(id, px, qty);
    } else if (r < 30) {
      f.market(id, acct, side, qty);
    } else if (r < 31) {
      f.send(KillSwitch{.account_id = acct, .engaged = rng() % 2 == 0, .cancel_open = false});
    } else if (r < 32) {
      f.send(SetSession{.state = static_cast<SessionState>(rng() % 4)});
    } else {
      const TimeInForce tifs[] = {TimeInForce::GTC, TimeInForce::Day, TimeInForce::IOC,
                                  TimeInForce::FOK};
      f.send(NewOrder{.order_id = id, .account_id = acct, .side = side, .tif = tifs[rng() % 4],
                      .stp = static_cast<StpMode>(rng() % 3), .price = px, .qty = qty});
    }
    ASSERT_NO_THROW(f.engine.check_invariants()) << "after command " << f.seq;
  }
}

// --- Admin at runtime ----------------------------------------------------------------------

TEST_F(Risk, LimitChangesApplyFromTheNextCommandAndKeepOpenOrders) {
  limit(1, 1, B, 100, 50);
  set_instrument({.max_qty = 10});
  EXPECT_TRUE(rejected(limit(2, 1, B, 100, 50), RejectReason::MaxQty));
  EXPECT_EQ(engine.book().leaves_qty(1), 50);  // tightening never touches open orders
}

TEST_F(Risk, SetAccountLimitsReplacesTheWholeSet) {
  set_account(1, {.max_notional = 100, .max_open_orders = 5});
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 2), RejectReason::MaxNotional));
  set_account(1, {.max_open_orders = 5});
  EXPECT_TRUE(acked(limit(2, 1, B, 100, 2)));
}

TEST_F(Risk, InvalidLimitsAreRefusedAndChangeNothing) {
  set_instrument({.max_qty = 10});
  const std::vector<InstrumentLimits> bad_instrument = {
      {.max_qty = 0}, {.band_bps = -1}, {.band_bps = kMaxBandBps + 1}, {.reference_price = 0}};
  for (const auto& l : bad_instrument) {
    const auto ev = send(SetInstrumentLimits{.limits = l});  // before reading seq
    EXPECT_EQ(ev, (std::vector<Event>{AdminReject{.seq_no = seq,
                                              .reason = AdminRejectReason::InvalidLimits}}));
  }
  const std::vector<AccountLimits> bad_account = {
      {.max_notional = -1}, {.position_limit = -1}, {.credit = -1}, {.max_open_orders = -1},
      {.rate = RateLimit{.per_sec = 0, .burst = 1}},
      {.rate = RateLimit{.per_sec = 1, .burst = 0}},
      {.rate = RateLimit{.per_sec = kMaxRate + 1, .burst = 1}},
      {.rate = RateLimit{.per_sec = 1, .burst = kMaxRate + 1}}};
  for (const auto& l : bad_account) {
    const auto ev = send(SetAccountLimits{.account_id = 1, .limits = l});  // before reading seq
    EXPECT_EQ(ev, (std::vector<Event>{AdminReject{.seq_no = seq,
                                              .reason = AdminRejectReason::InvalidLimits}}));
  }
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 11), RejectReason::MaxQty));
}

TEST_F(Risk, RiskReducingReplaceSkipsLimitChecks) {
  set_account(1, {.position_limit = 10});
  limit(1, 1, B, 100, 10);
  set_account(1, {.position_limit = 5});
  EXPECT_TRUE(acked(replace(1, 100, 8)));  // same price, smaller: allowed
  EXPECT_TRUE(rejected(replace(1, 101, 8), RejectReason::PositionLimit));
  EXPECT_TRUE(rejected(replace(1, 100, 9), RejectReason::PositionLimit));  // increase
}

TEST_F(Risk, ReplaceIsCheckedOnItsNewTotals) {
  set_account(1, {.max_notional = 1'000, .credit = 1'500});
  limit(1, 1, B, 100, 10);
  limit(2, 1, B, 50, 10);  // open notional 1500
  EXPECT_TRUE(rejected(replace(1, 101, 10), RejectReason::MaxNotional));
  EXPECT_TRUE(rejected(replace(2, 51, 10), RejectReason::InsufficientCredit));  // +10
  EXPECT_TRUE(acked(replace(2, 49, 10)));
}

}  // namespace
}  // namespace mf

namespace mf {
namespace {

// Risk state lists accounts in ascending id order however they were created (M6: the
// account table is hashed, so state() must sort).
TEST_F(Risk, StateListsAccountsInAscendingIdOrder) {
  const AccountId ids[] = {700, 3, 1'000'000'007, 42, 5, 9'999};
  const AccountLimits limits{.rate = RateLimit{.per_sec = 10, .burst = 10}};
  for (const AccountId id : ids) {
    set_account(id, limits);
    kill(id, true);
    kill(id, false);
  }
  kill(42, true);
  kill(5, true);
  for (std::size_t i = 0; i < std::size(ids); ++i) {
    // Opposite sides of the same account would self-trade: pair each with account 1.
    limit(100 + 2 * i, 1, Side::Sell, 1'000, 1);
    if (ids[i] != 42 && ids[i] != 5) limit(101 + 2 * i, ids[i], Side::Buy, 1'000, 1);
  }
  const RiskState s = engine.risk().state();
  const auto ascending = [](const auto& items, auto key) {
    for (std::size_t i = 1; i < items.size(); ++i) {
      if (key(items[i - 1]) >= key(items[i])) return false;
    }
    return true;
  };
  EXPECT_EQ(s.account_limits.size(), std::size(ids));
  EXPECT_TRUE(ascending(s.account_limits, [](const auto& e) { return e.account_id; }));
  EXPECT_EQ(s.killed_accounts, (std::vector<AccountId>{5, 42}));
  EXPECT_GE(s.positions.size(), 4u);
  EXPECT_TRUE(ascending(s.positions, [](const auto& p) { return p.account_id; }));
  EXPECT_GE(s.buckets.size(), 4u);
  EXPECT_TRUE(ascending(s.buckets, [](const auto& b) { return b.account_id; }));
  // And it restores to the same state.
  EXPECT_EQ(Engine::from_state(engine.state())->state(), engine.state());
}

}  // namespace
}  // namespace mf
