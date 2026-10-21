#include <gtest/gtest.h>

#include <filesystem>
#include <random>
#include <string>
#include <vector>

#include "mf/order_book.hpp"
#include "mf/snapshot.hpp"
#include "temp_dir.hpp"
#include "test_helpers.hpp"

// Book state capture/restore and the snapshot file format (docs/snapshot-format.md).

namespace mf {
namespace {

using test::read_bytes;
using test::run;
using test::TempDir;
using test::write_bytes;

NewOrder limit(SeqNo seq, OrderId id, AccountId acct, Side side, Price price, Qty qty) {
  return {.seq_no = seq, .order_id = id, .account_id = acct, .side = side,
          .price = price, .qty = qty};
}

// Several levels per side, partial fills, a replaced order, post-only and mixed STP
// modes, and ids used by orders that are no longer resting.
void build_rich_book(OrderBook& book) {
  run(book, limit(1, 1, 1, Side::Sell, 101, 10));
  run(book, limit(2, 2, 2, Side::Sell, 101, 5));
  auto po = limit(3, 3, 3, Side::Sell, 103, 7);
  po.post_only = true;
  po.stp = StpMode::CancelBoth;
  run(book, po);
  run(book, limit(4, 4, 4, Side::Buy, 99, 8));
  auto co = limit(5, 5, 1, Side::Buy, 98, 6);
  co.stp = StpMode::CancelOldest;
  run(book, co);
  run(book, limit(6, 6, 4, Side::Buy, 101, 4));                  // partially fills 1
  run(book, Replace{.seq_no = 7, .order_id = 4, .price = 99, .qty = 12});  // loses priority
  run(book, limit(8, 8, 2, Side::Buy, 99, 3));
  run(book, limit(9, 9, 3, Side::Buy, 97, 2));
  run(book, Cancel{.seq_no = 10, .order_id = 9});                // id 9 used, not resting
}

TEST(BookState, CapturesLevelsQueuesAndCounters) {
  OrderBook book;
  build_rich_book(book);
  const BookState s = book.state();

  EXPECT_EQ(s.last_seq_no, 10u);
  EXPECT_EQ(s.next_trade_id, 2u);
  EXPECT_EQ(s.used_ids, (std::vector<OrderId>{1, 2, 3, 4, 5, 6, 8, 9}));
  ASSERT_EQ(s.asks.size(), 2u);
  EXPECT_EQ(s.asks[0].price, 101);
  ASSERT_EQ(s.asks[0].orders.size(), 2u);
  EXPECT_EQ(s.asks[0].orders[0],
            (BookState::RestingOrder{.order_id = 1, .account_id = 1, .qty = 10,
                                     .leaves_qty = 6, .filled_qty = 4,
                                     .priority_seq_no = 1, .post_only = false,
                                     .stp = StpMode::CancelNewest}));
  EXPECT_EQ(s.asks[1].orders[0].post_only, true);
  EXPECT_EQ(s.asks[1].orders[0].stp, StpMode::CancelBoth);
  ASSERT_EQ(s.bids.size(), 2u);
  EXPECT_EQ(s.bids[0].price, 99);
  // Order 4 was replaced up at seq 7, so it queues behind nothing yet but ahead of 8.
  ASSERT_EQ(s.bids[0].orders.size(), 2u);
  EXPECT_EQ(s.bids[0].orders[0].order_id, 4u);
  EXPECT_EQ(s.bids[0].orders[0].priority_seq_no, 7u);
  EXPECT_EQ(s.bids[0].orders[1].order_id, 8u);
  EXPECT_EQ(s.bids[1].orders[0].stp, StpMode::CancelOldest);
}

TEST(BookState, EmptyBook) {
  OrderBook book;
  const BookState s = book.state();
  EXPECT_EQ(s, BookState{});
  const auto restored = OrderBook::from_state(s);
  EXPECT_EQ(restored->order_count(), 0u);
  EXPECT_EQ(restored->state(), s);
}

TEST(BookState, RestoreRebuildsTheSameBook) {
  OrderBook book;
  build_rich_book(book);
  const auto restored = OrderBook::from_state(book.state());
  EXPECT_EQ(restored->state(), book.state());
  restored->check_invariants();
  for (const Side side : {Side::Buy, Side::Sell}) {
    EXPECT_EQ(restored->level_prices(side), book.level_prices(side));
    for (const Price p : book.level_prices(side)) {
      EXPECT_EQ(restored->level_orders(side, p), book.level_orders(side, p));
      EXPECT_EQ(restored->level_qty(side, p), book.level_qty(side, p));
      EXPECT_EQ(restored->level_order_count(side, p), book.level_order_count(side, p));
    }
  }
}

// The real test of a snapshot: the restored book's future output is identical. Covers
// FIFO order, next_trade_id, used ids and the seq_no check.
TEST(BookState, RestoredBookProducesIdenticalEvents) {
  OrderBook book;
  build_rich_book(book);
  const auto restored = OrderBook::from_state(book.state());

  std::mt19937_64 rng(4242);
  OrderId next_id = 1;  // deliberately collides with used ids early on
  for (SeqNo seq = 11; seq < 3000; ++seq) {
    Command c;
    const auto r = rng() % 10;
    const Side side = rng() % 2 ? Side::Buy : Side::Sell;
    if (r < 2) {
      c = Cancel{.seq_no = seq, .order_id = rng() % next_id + 1};
    } else if (r < 3) {
      c = Replace{.seq_no = seq, .order_id = rng() % next_id + 1,
                  .price = static_cast<Price>(95 + rng() % 10),
                  .qty = static_cast<Qty>(1 + rng() % 20)};
    } else {
      NewOrder o = limit(seq, next_id++, 1 + rng() % 4, side,
                         static_cast<Price>(95 + rng() % 10), 1 + rng() % 20);
      o.tif = static_cast<TimeInForce>(rng() % 3);
      o.stp = static_cast<StpMode>(rng() % 3);
      c = o;
    }
    ASSERT_EQ(run(*restored, c), run(book, c)) << "seq " << seq;
  }
  EXPECT_EQ(restored->state(), book.state());
  EXPECT_THROW(run(*restored, Cancel{.seq_no = 5, .order_id = 1}), InvariantViolation);
}

TEST(BookState, RestoredBookStillRejectsUsedIds) {
  OrderBook book;
  build_rich_book(book);
  const auto restored = OrderBook::from_state(book.state());
  const auto events = run(*restored, limit(11, 9, 1, Side::Buy, 90, 1));  // 9 was cancelled
  EXPECT_EQ(events, (std::vector<Event>{Reject{.seq_no = 11, .order_id = 9,
                                               .reason = RejectReason::DuplicateOrderId}}));
}

TEST(BookState, RestoreRejectsInconsistentStates) {
  OrderBook book;
  build_rich_book(book);
  const BookState good = book.state();
  const auto expect_bad = [](BookState s, const char* what) {
    EXPECT_THROW(OrderBook::from_state(s), InvariantViolation) << what;
  };

  BookState s = good;
  std::swap(s.bids[0], s.bids[1]);
  expect_bad(s, "levels not best first");
  s = good;
  s.asks[1].orders.clear();
  expect_bad(s, "empty level");
  s = good;
  s.asks[0].orders[0].leaves_qty = 0;
  s.asks[0].orders[0].filled_qty = 10;
  expect_bad(s, "resting with no open quantity");
  s = good;
  s.asks[0].orders[0].filled_qty = 3;
  expect_bad(s, "quantity not conserved");
  s = good;
  s.bids[1].orders[0].order_id = 1;
  expect_bad(s, "duplicate resting id");
  s = good;
  s.used_ids.erase(s.used_ids.begin());  // id 1 rests but is not used
  expect_bad(s, "resting id missing from used_ids");
  s = good;
  std::swap(s.used_ids[0], s.used_ids[1]);
  expect_bad(s, "used_ids not ascending");
  s = good;
  s.bids[0].price = 102;
  expect_bad(s, "crossed book");
  s = good;
  s.bids[0].price = 0;
  s.bids[1].price = -1;
  expect_bad(s, "non-positive price");
  s = good;
  s.next_trade_id = 0;
  expect_bad(s, "trade id 0");
}

// --- File format ------------------------------------------------------------------

Snapshot rich_snapshot() {
  OrderBook book;
  build_rich_book(book);
  return {.book = book.state(), .last_timestamp = 123'456'789, .journal_offset = 4242};
}

TEST(SnapshotFormat, RoundTripsAndReencodesToIdenticalBytes) {
  const Snapshot snap = rich_snapshot();
  const std::string bytes = encode_snapshot(snap);
  EXPECT_EQ(bytes.substr(0, 8), kSnapshotMagic);
  const Snapshot decoded = decode_snapshot(bytes);
  EXPECT_EQ(decoded, snap);
  EXPECT_EQ(encode_snapshot(decoded), bytes);
  EXPECT_EQ(decode_snapshot(encode_snapshot(Snapshot{})), Snapshot{});
}

// used_ids lives in a hash set; the snapshot must not depend on its iteration order.
TEST(SnapshotFormat, SameStateGivesSameBytesWhateverTheHistory) {
  OrderBook up;
  OrderBook down;
  SeqNo seq = 1;
  for (OrderId i = 1; i <= 500; ++i, seq += 2) {
    const OrderId a = i;
    const OrderId b = 501 - i;
    run(up, limit(seq, a, 1, Side::Buy, 100, 1));
    run(up, Cancel{.seq_no = seq + 1, .order_id = a});
    run(down, limit(seq, b, 1, Side::Buy, 100, 1));
    run(down, Cancel{.seq_no = seq + 1, .order_id = b});
  }
  EXPECT_EQ(encode_snapshot({.book = up.state()}), encode_snapshot({.book = down.state()}));
}

TEST(SnapshotFormat, DetectsEveryCorruptByteAndEveryTruncation) {
  const std::string bytes = encode_snapshot(rich_snapshot());
  for (std::size_t i = 0; i < bytes.size(); ++i) {
    std::string damaged = bytes;
    damaged[i] ^= 0x04;
    EXPECT_THROW(decode_snapshot(damaged), SnapshotError) << "byte " << i;
  }
  for (std::size_t cut = 0; cut < bytes.size(); ++cut) {
    EXPECT_THROW(decode_snapshot(bytes.substr(0, cut)), SnapshotError) << "cut " << cut;
  }
  EXPECT_THROW(decode_snapshot(bytes + "x"), SnapshotError);
}

TEST(SnapshotFormat, ReportsAnUnsupportedVersion) {
  std::string bytes = encode_snapshot(Snapshot{});
  bytes[8] = 9;
  try {
    decode_snapshot(bytes);
    FAIL() << "expected SnapshotError";
  } catch (const SnapshotError& e) {
    EXPECT_NE(std::string(e.what()).find("version 9"), std::string::npos) << e.what();
  }
}

TEST(SnapshotFile, WritesAtomicallyAndReadsBack) {
  TempDir dir;
  const Snapshot snap = rich_snapshot();
  write_snapshot_file(dir.file("s.mfsnap"), snap);
  EXPECT_EQ(read_snapshot_file(dir.file("s.mfsnap")), snap);
  EXPECT_FALSE(std::filesystem::exists(dir.file("s.mfsnap.tmp")));
  EXPECT_EQ(read_bytes(dir.file("s.mfsnap")), encode_snapshot(snap));
  EXPECT_THROW(read_snapshot_file(dir.file("missing.mfsnap")), SnapshotError);
  write_bytes(dir.file("junk.mfsnap"), "not a snapshot");
  EXPECT_THROW(read_snapshot_file(dir.file("junk.mfsnap")), SnapshotError);
}

}  // namespace
}  // namespace mf
