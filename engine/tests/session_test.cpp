#include <gtest/gtest.h>

#include <vector>

#include "mf/engine.hpp"
#include "risk_fixture.hpp"
#include "test_helpers.hpp"

// Session states (M5, docs/risk.md): transitions, what each state accepts, DAY expiry
// at close and the used-id reset at session open.

namespace mf {
namespace {

using test::acked;
using test::events_of;
using test::rejected;
using test::RiskFixture;

constexpr Side B = Side::Buy;
constexpr Side S = Side::Sell;

class Session : public RiskFixture, public ::testing::Test {};

// Drives a fresh engine (CONTINUOUS) into `state` through allowed transitions.
void enter(RiskFixture& f, SessionState state) {
  switch (state) {
    case SessionState::Continuous: return;
    case SessionState::Halted: f.session(SessionState::Halted); return;
    case SessionState::Closed: f.session(SessionState::Closed); return;
    case SessionState::PreOpen:
      f.session(SessionState::Closed);
      f.session(SessionState::PreOpen);
      return;
  }
}

TEST(SessionTransitions, OnlyTheDocumentedTransitionsAreAllowed) {
  using enum SessionState;
  const SessionState all[] = {PreOpen, Continuous, Halted, Closed};
  const auto allowed = [](SessionState from, SessionState to) {
    return (from == Closed && to == PreOpen) || (from == PreOpen && to == Continuous) ||
           (from == Continuous && to == Halted) || (from == Halted && to == Continuous) ||
           (from != Closed && to == Closed);
  };
  for (const auto from : all) {
    for (const auto to : all) {
      RiskFixture f;
      enter(f, from);
      ASSERT_EQ(f.engine.risk().session(), from);
      const auto ev = f.send(SetSession{.state = to});
      if (allowed(from, to)) {
        EXPECT_EQ(ev, (std::vector<Event>{SessionChanged{.seq_no = f.seq, .state = to}}))
            << int(from) << " -> " << int(to);
        EXPECT_EQ(f.engine.risk().session(), to);
      } else {
        EXPECT_EQ(ev, (std::vector<Event>{AdminReject{
                          .seq_no = f.seq, .reason = AdminRejectReason::InvalidTransition}}))
            << int(from) << " -> " << int(to);
        EXPECT_EQ(f.engine.risk().session(), from);
      }
    }
  }
}

TEST_F(Session, AFreshEngineIsContinuous) {
  EXPECT_EQ(engine.risk().session(), SessionState::Continuous);
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 1)));
}

TEST_F(Session, OnlyContinuousAcceptsNewOrdersAndReplacesButCancelsAlwaysWork) {
  for (const auto state : {SessionState::PreOpen, SessionState::Halted, SessionState::Closed}) {
    RiskFixture f;
    f.limit(1, 1, B, 100, 5);
    f.limit(2, 1, B, 99, 5);
    enter(f, state);
    EXPECT_TRUE(rejected(f.limit(3, 1, B, 100, 1), RejectReason::MarketClosed)) << int(state);
    EXPECT_TRUE(rejected(f.market(4, 2, S, 1), RejectReason::MarketClosed)) << int(state);
    EXPECT_TRUE(rejected(f.replace(1, 100, 4), RejectReason::MarketClosed)) << int(state);
    const auto ev = f.cancel(1);  // before reading f.seq
    EXPECT_EQ(ev, (std::vector<Event>{Cancelled{.seq_no = f.seq, .order_id = 1,
                                                          .cancelled_qty = 5,
                                                          .reason = CancelReason::User}}))
        << int(state);
  }
}

TEST_F(Session, HaltKeepsRestingOrdersAndResumes) {
  limit(1, 1, B, 100, 5, TimeInForce::Day);
  session(SessionState::Halted);
  EXPECT_EQ(engine.book().leaves_qty(1), 5);  // a halt expires nothing
  session(SessionState::Continuous);
  EXPECT_TRUE(acked(limit(2, 2, S, 100, 5)));
}

TEST_F(Session, DayOrdersExpireAtCloseInBookOrderAndGtcOrdersSurvive) {
  limit(1, 1, B, 99, 5, TimeInForce::Day);
  limit(2, 2, B, 100, 3, TimeInForce::GTC);
  limit(3, 3, B, 100, 4, TimeInForce::Day);
  limit(4, 1, S, 105, 2, TimeInForce::Day);
  limit(5, 2, S, 104, 1, TimeInForce::GTC);
  const auto ev = session(SessionState::Closed);
  const SeqNo s = seq;
  EXPECT_EQ(ev, (std::vector<Event>{
                    SessionChanged{.seq_no = s, .state = SessionState::Closed},
                    Cancelled{.seq_no = s, .order_id = 3, .cancelled_qty = 4,
                              .reason = CancelReason::Expired},
                    Cancelled{.seq_no = s, .order_id = 1, .cancelled_qty = 5,
                              .reason = CancelReason::Expired},
                    Cancelled{.seq_no = s, .order_id = 4, .cancelled_qty = 2,
                              .reason = CancelReason::Expired},
                }));
  EXPECT_EQ(engine.book().order_count(), 2u);
  EXPECT_EQ(engine.risk().exposure(1), RiskGate::Exposure{});
  EXPECT_EQ(engine.risk().exposure(3), RiskGate::Exposure{});
}

TEST_F(Session, PartlyFilledDayOrderRestsAsDayAndExpires) {
  limit(1, 2, S, 100, 2);
  limit(2, 1, B, 100, 5, TimeInForce::Day);  // fills 2, rests 3 as DAY
  replace(2, 99, 6);                          // re-priced: still DAY
  const auto ev = session(SessionState::Closed);
  EXPECT_EQ(events_of<Cancelled>(ev),
            (std::vector<Cancelled>{{.seq_no = seq, .order_id = 2, .cancelled_qty = 4,
                                     .reason = CancelReason::Expired}}));
}

TEST_F(Session, DayIsOnlyValidOnLimitOrders) {
  EXPECT_TRUE(rejected(send(NewOrder{.order_id = 1, .account_id = 1, .side = B,
                                     .type = OrderType::Market, .tif = TimeInForce::Day,
                                     .qty = 1}),
                       RejectReason::InvalidOrderType));
  EXPECT_TRUE(acked(send(NewOrder{.order_id = 2, .account_id = 1, .side = B,
                                  .tif = TimeInForce::Day, .post_only = true, .price = 100,
                                  .qty = 1})));
  EXPECT_TRUE(rejected(send(NewOrder{.order_id = 3, .account_id = 1, .side = B,
                                     .tif = TimeInForce::IOC, .post_only = true,
                                     .price = 100, .qty = 1}),
                       RejectReason::InvalidOrderType));
}

TEST_F(Session, SessionOpenResetsUsedIdsExceptRestingOnes) {
  limit(1, 1, B, 100, 5);
  limit(2, 2, S, 100, 5);  // 1 and 2 fill: ids used, nothing resting
  limit(3, 1, B, 90, 5);   // GTC, still resting at the next open
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 1), RejectReason::DuplicateOrderId));
  session(SessionState::Closed);
  session(SessionState::PreOpen);
  session(SessionState::Continuous);
  EXPECT_TRUE(acked(limit(1, 1, B, 95, 1)));
  EXPECT_TRUE(acked(limit(2, 2, S, 120, 1)));
  EXPECT_TRUE(rejected(limit(3, 1, B, 90, 1), RejectReason::DuplicateOrderId));
  EXPECT_EQ(engine.state().book.used_ids, (std::vector<OrderId>{1, 2, 3}));
}

TEST_F(Session, IdsAreNotResetByAHalt) {
  limit(1, 1, B, 100, 5);
  cancel(1);
  session(SessionState::Halted);
  session(SessionState::Continuous);
  EXPECT_TRUE(rejected(limit(1, 1, B, 100, 1), RejectReason::DuplicateOrderId));
}

}  // namespace
}  // namespace mf
