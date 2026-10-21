#include <gtest/gtest.h>

#include <stdexcept>
#include <vector>

#include "mf/clock.hpp"
#include "mf/journal.hpp"
#include "mf/order_book.hpp"
#include "mf/sequencer.hpp"
#include "temp_dir.hpp"
#include "test_helpers.hpp"

namespace mf {
namespace {

using test::run;
using test::TempDir;

TEST(SimulatedClock, SetAndAdvanceMoveTimeForward) {
  SimulatedClock clock(100);
  EXPECT_EQ(clock.now(), 100);
  EXPECT_EQ(clock.now(), 100);  // no step: time only moves when told
  clock.advance(5);
  EXPECT_EQ(clock.now(), 105);
  clock.set(105);  // equal is allowed
  clock.set(200);
  EXPECT_EQ(clock.peek(), 200);
  EXPECT_EQ(clock.now(), 200);
}

TEST(SimulatedClock, AutoStepAdvancesAfterEachRead) {
  SimulatedClock clock(1000, 10);
  EXPECT_EQ(clock.now(), 1000);
  EXPECT_EQ(clock.now(), 1010);
  EXPECT_EQ(clock.peek(), 1020);
}

TEST(SimulatedClock, NeverGoesBackwards) {
  SimulatedClock clock(100);
  EXPECT_THROW(clock.set(99), std::invalid_argument);
  EXPECT_THROW(clock.advance(-1), std::invalid_argument);
  EXPECT_THROW(SimulatedClock(0, -1), std::invalid_argument);
  EXPECT_EQ(clock.now(), 100);
}

NewOrder bid(OrderId id, Price price, Qty qty) {
  return {.seq_no = 999, .order_id = id, .account_id = 1, .side = Side::Buy,
          .price = price, .qty = qty};
}

TEST(Sequencer, AssignsGaplessSeqNosAndStampsTheClock) {
  TempDir dir;
  SimulatedClock clock(5000, 7);
  JournalWriter journal(dir.file("j.mfj"));
  Sequencer sequencer(clock, journal);

  const auto r1 = sequencer.sequence(bid(1, 100, 5));  // incoming seq_no is ignored
  const auto r2 = sequencer.sequence(Cancel{.seq_no = 0, .order_id = 1});
  EXPECT_EQ(r1.seq_no(), 1u);
  EXPECT_EQ(r1.timestamp, 5000);
  EXPECT_EQ(std::get<NewOrder>(r1.command).seq_no, 1u);
  EXPECT_EQ(r2.seq_no(), 2u);
  EXPECT_EQ(r2.timestamp, 5007);
  EXPECT_EQ(sequencer.last_seq_no(), 2u);
}

// Write-ahead: the record is in the journal file before the caller sees it.
TEST(Sequencer, JournalsBeforeReturning) {
  TempDir dir;
  SimulatedClock clock(1);
  JournalWriter journal(dir.file("j.mfj"));
  Sequencer sequencer(clock, journal);
  const auto record = sequencer.sequence(bid(1, 100, 5));

  JournalReader reader(dir.file("j.mfj"));
  EXPECT_EQ(reader.next(), std::optional<JournalRecord>{record});
  EXPECT_EQ(reader.next(), std::nullopt);
}

// Rejected commands are journaled too: the reject depends on book state, so replay has
// to re-run the command to reproduce it.
TEST(Sequencer, JournalsCommandsTheBookRejects) {
  TempDir dir;
  SimulatedClock clock(1, 1);
  JournalWriter journal(dir.file("j.mfj"));
  Sequencer sequencer(clock, journal);
  OrderBook book;

  const std::vector<Command> commands = {
      Cancel{.order_id = 77},                                          // UNKNOWN_ORDER
      NewOrder{.order_id = 2, .account_id = 1, .side = Side::Buy,
               .tif = TimeInForce::FOK, .price = 100, .qty = 5},       // FOK_NOT_FILLABLE
      bid(3, 100, 0),                                                  // INVALID_QTY
  };
  for (const auto& c : commands) {
    const auto events = run(book, sequencer.sequence(c).command);
    ASSERT_EQ(events.size(), 1u);
    EXPECT_TRUE(std::holds_alternative<Reject>(events[0]));
  }

  JournalReader reader(dir.file("j.mfj"));
  std::size_t n = 0;
  while (reader.next()) ++n;
  EXPECT_EQ(n, commands.size());
}

}  // namespace
}  // namespace mf
