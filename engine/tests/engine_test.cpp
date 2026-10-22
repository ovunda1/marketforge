// Engine::process caller contract (M7): a stale seq_no or a timestamp going backwards is
// a SequenceError, thrown before the engine changes any state, so the engine stays
// usable. The Python bindings rely on the distinction: any other exception from
// process() may come after state changed, and marks the binding's engine as failed.

#include <gtest/gtest.h>

#include <vector>

#include "mf/engine.hpp"
#include "risk_fixture.hpp"

namespace mf::test {
namespace {

constexpr Side B = Side::Buy;
constexpr Side S = Side::Sell;

class EngineContract : public ::testing::Test, public RiskFixture {};

TEST_F(EngineContract, SequenceErrorIsAnInvariantViolation) {
  static_assert(std::is_base_of_v<InvariantViolation, SequenceError>);
  limit(1, 1, B, 100, 5);
  EXPECT_THROW(run(engine, Cancel{.seq_no = 1, .order_id = 1}), InvariantViolation);
}

TEST_F(EngineContract, StaleSeqNoChangesNothingAndTheEngineStaysUsable) {
  limit(1, 1, B, 100, 5);
  limit(2, 2, S, 101, 5);
  const EngineState before = engine.state();
  for (const Command& stale : std::vector<Command>{
           NewOrder{.seq_no = 2, .order_id = 3, .account_id = 3, .side = S, .price = 100, .qty = 1},
           Cancel{.seq_no = 1, .order_id = 1},
           Replace{.seq_no = 2, .order_id = 1, .price = 101, .qty = 5},
           SetSession{.seq_no = 2, .state = SessionState::Halted},
           KillSwitch{.seq_no = 1, .engaged = true, .cancel_open = true},
       }) {
    std::vector<Event> events;
    EXPECT_THROW(engine.process(stale, now, events), SequenceError);
    EXPECT_TRUE(events.empty());
    EXPECT_EQ(engine.state(), before);
  }
  EXPECT_TRUE(acked(limit(3, 3, S, 100, 1)));  // seq 3 trades against order 1
  EXPECT_EQ(engine.book().leaves_qty(1), 4);
}

TEST_F(EngineContract, TimestampGoingBackwardsIsASequenceErrorWithoutRateLimits) {
  at(1'000);
  limit(1, 1, B, 100, 5);
  const EngineState before = engine.state();
  at(999);
  std::vector<Event> events;
  EXPECT_THROW(engine.process(Cancel{.seq_no = 2, .order_id = 1}, now, events), SequenceError);
  EXPECT_TRUE(events.empty());
  EXPECT_EQ(engine.state(), before);
  // The rejected command did not consume seq_no 2.
  at(1'000);
  EXPECT_TRUE(acked(cancel(1)));
  EXPECT_EQ(seq, 2u);
}

TEST_F(EngineContract, TimestampGoingBackwardsWithARateLimitChangesNoBucket) {
  set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 5}});
  at(1'000);
  limit(1, 1, B, 100, 1);
  const EngineState before = engine.state();
  at(999);
  EXPECT_THROW(limit(2, 1, B, 100, 1), SequenceError);
  EXPECT_EQ(engine.state(), before);
}

TEST_F(EngineContract, EqualTimestampsAreAllowed) {
  at(5);
  EXPECT_TRUE(acked(limit(1, 1, B, 100, 5)));
  EXPECT_TRUE(acked(limit(2, 1, B, 99, 5)));
}

TEST_F(EngineContract, RestoredEngineRefusesTimeBeforeTheGivenLastTimestamp) {
  at(1'000);
  limit(1, 1, B, 100, 5);
  const auto restored = Engine::from_state(engine.state(), 1'000);
  std::vector<Event> events;
  EXPECT_THROW(restored->process(Cancel{.seq_no = 2, .order_id = 1}, 999, events), SequenceError);
  EXPECT_NO_THROW(restored->process(Cancel{.seq_no = 2, .order_id = 1}, 1'000, events));
}

TEST_F(EngineContract, RestoredEngineRefusesTimeBeforeAnyRateBucketRefill) {
  // Without a last timestamp, the latest bucket refill is the lower bound: an earlier
  // time would otherwise reach the rate limiter after the checks began.
  set_account(1, {.rate = RateLimit{.per_sec = 1, .burst = 5}});
  at(2'000);
  limit(1, 1, B, 100, 1);
  const auto restored = Engine::from_state(engine.state());
  std::vector<Event> events;
  EXPECT_THROW(restored->process(Cancel{.seq_no = 3, .order_id = 1}, 1'999, events), SequenceError);
  EXPECT_EQ(restored->state(), engine.state());
}

}  // namespace
}  // namespace mf::test
