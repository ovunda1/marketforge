// Price levels far apart, and the best price moving a long way and back (M6). The flat
// tick array keeps only a window of prices around the best in its array, so these are
// the cases where it differs from a sorted map: prices outside the window, the window
// moving, and levels moving in and out of it. Every test also runs the full invariant
// walk and a snapshot round trip.

#include <gtest/gtest.h>

#include <cstdint>
#include <limits>
#include <random>
#include <vector>

#include "mf/order_book.hpp"
#include "test_helpers.hpp"

namespace mf {
namespace {

using test::events_of;

class PriceLevels : public ::testing::Test {
 protected:
  std::vector<Event> add(Side side, Price price, Qty qty, OrderId id, AccountId account = 1,
                         TimeInForce tif = TimeInForce::GTC, OrderType type = OrderType::Limit) {
    return step(NewOrder{.seq_no = ++seq_, .order_id = id, .account_id = account, .side = side,
                         .type = type, .tif = tif, .price = price, .qty = qty});
  }
  std::vector<Event> cancel(OrderId id) { return step(Cancel{.seq_no = ++seq_, .order_id = id}); }
  std::vector<Event> replace(OrderId id, Price price, Qty qty) {
    return step(Replace{.seq_no = ++seq_, .order_id = id, .price = price, .qty = qty});
  }

  std::vector<Event> step(const OrderCommand& command) {
    auto events = test::run(book_, command);
    book_.check_invariants();
    return events;
  }

  void expect_round_trip() const {
    EXPECT_EQ(OrderBook::from_state(book_.state())->state(), book_.state());
  }

  OrderBook book_;
  SeqNo seq_{};
};

constexpr Price kHuge = std::numeric_limits<Price>::max() / 2;

TEST_F(PriceLevels, FarApartPricesAreOrderedBestFirst) {
  const std::vector<Price> bids = {1, 10, 1'000'000, 1'000'000'000'000};
  const std::vector<Price> asks = {kHuge, 2'000'000'000'000, 1'000'000'000'001};
  OrderId id = 1;
  for (const Price p : bids) add(Side::Buy, p, 5, id++);
  for (const Price p : asks) add(Side::Sell, p, 5, id++);
  EXPECT_EQ(book_.level_prices(Side::Buy),
            (std::vector<Price>{1'000'000'000'000, 1'000'000, 10, 1}));
  EXPECT_EQ(book_.level_prices(Side::Sell),
            (std::vector<Price>{1'000'000'000'001, 2'000'000'000'000, kHuge}));
  EXPECT_EQ(book_.best_bid(), 1'000'000'000'000);
  EXPECT_EQ(book_.best_ask(), 1'000'000'000'001);
  expect_round_trip();
}

TEST_F(PriceLevels, BestJumpsFarAwayAndComesBack) {
  add(Side::Buy, 100, 5, 1);
  add(Side::Buy, 99, 5, 2);
  add(Side::Buy, 5'000'000, 5, 3);  // far above everything: the new best
  EXPECT_EQ(book_.best_bid(), 5'000'000);
  add(Side::Buy, 4'999'999, 5, 4);
  cancel(3);
  EXPECT_EQ(book_.best_bid(), 4'999'999);
  cancel(4);
  EXPECT_EQ(book_.best_bid(), 100);  // back to the levels left behind
  EXPECT_EQ(book_.level_prices(Side::Buy), (std::vector<Price>{100, 99}));
  expect_round_trip();
}

TEST_F(PriceLevels, FifoAndLinksSurviveTheBestMovingAway) {
  for (OrderId id = 1; id <= 3; ++id) add(Side::Sell, 1'000, 10, id);
  for (OrderId id = 4; id <= 6; ++id) add(Side::Sell, 1'001, 10, id);
  add(Side::Sell, 10, 10, 7);  // far below: the best ask moves a long way
  add(Side::Sell, 20, 10, 8);
  EXPECT_EQ(book_.level_orders(Side::Sell, 1'000), (std::vector<OrderId>{1, 2, 3}));
  cancel(2);  // the order's level pointer must still be right
  EXPECT_EQ(book_.level_orders(Side::Sell, 1'000), (std::vector<OrderId>{1, 3}));

  // A buy sweeps everything: price order, FIFO within each level.
  const auto events = add(Side::Buy, 1'001, 70, 9, 2);
  std::vector<OrderId> makers;
  for (const auto& t : events_of<Trade>(events)) makers.push_back(t.maker_order_id);
  EXPECT_EQ(makers, (std::vector<OrderId>{7, 8, 1, 3, 4, 5, 6}));
  EXPECT_EQ(book_.best_ask(), std::nullopt);
  EXPECT_EQ(book_.best_bid(), std::nullopt);  // 70 = 7 x 10: fully filled
  expect_round_trip();
}

TEST_F(PriceLevels, MarketOrderSweepsAcrossAnyGap) {
  add(Side::Buy, 50, 1, 1);
  add(Side::Buy, 1'000'000, 1, 2);
  add(Side::Buy, 3, 1, 3);
  const auto events = add(Side::Sell, 0, 3, 4, 2, TimeInForce::IOC, OrderType::Market);
  std::vector<Price> prices;
  for (const auto& t : events_of<Trade>(events)) prices.push_back(t.price);
  EXPECT_EQ(prices, (std::vector<Price>{1'000'000, 50, 3}));
  EXPECT_EQ(book_.level_count(Side::Buy), 0u);
}

TEST_F(PriceLevels, ReplaceMovesAnOrderFarAndBack) {
  add(Side::Buy, 500, 10, 1);
  add(Side::Buy, 500, 10, 2);
  replace(1, 9'000'000, 10);
  EXPECT_EQ(book_.best_bid(), 9'000'000);
  replace(1, 500, 10);  // back, at the end of the queue
  EXPECT_EQ(book_.level_orders(Side::Buy, 500), (std::vector<OrderId>{2, 1}));
  EXPECT_EQ(book_.level_count(Side::Buy), 1u);
  expect_round_trip();
}

TEST_F(PriceLevels, CrossingAtAFarPriceTradesAtTheRestingPrice) {
  add(Side::Sell, 7, 5, 1);
  const auto events = add(Side::Buy, kHuge, 5, 2, 2);
  ASSERT_EQ(events_of<Trade>(events).size(), 1u);
  EXPECT_EQ(events_of<Trade>(events)[0].price, 7);
}

// Seeded random flow over a huge price range, clustered near a drifting mid with jumps
// far away: the invariant walk (sorted levels, cached best, links) after every command
// and a snapshot round trip every few hundred.
TEST_F(PriceLevels, RandomWideFlowKeepsEveryInvariant) {
  std::mt19937_64 rng(6);
  Price mid = 1'000'000;
  std::vector<OrderId> ids;
  OrderId next = 1;
  for (int i = 0; i < 20'000; ++i) {
    if (rng() % 64 == 0) mid = std::max<Price>(10'000, mid + static_cast<Price>(rng() % 2'001) - 1'000);
    const auto r = rng() % 100;
    if (r < 30 && !ids.empty()) {
      const std::size_t k = rng() % ids.size();
      cancel(ids[k]);
      ids[k] = ids.back();
      ids.pop_back();
    } else if (r < 40 && !ids.empty()) {
      const Price off = static_cast<Price>(rng() % 3'000);
      replace(ids[rng() % ids.size()], mid + (rng() % 2 ? off : -off), 1 + static_cast<Qty>(rng() % 50));
    } else {
      const Side side = rng() % 2 ? Side::Buy : Side::Sell;
      Price off = static_cast<Price>(rng() % (rng() % 8 == 0 ? 5'000 : 20));
      if (rng() % 200 == 0) off = static_cast<Price>(rng() % 900'000);  // far away
      const bool aggressive = rng() % 5 == 0;
      const Price price = side == Side::Buy ? mid + (aggressive ? 3 : -1 - off)
                                            : mid - (aggressive ? 3 : -1 - off);
      add(side, price, 1 + static_cast<Qty>(rng() % 50), next, 1 + rng() % 8);
      ids.push_back(next++);
    }
    if (i % 500 == 0) expect_round_trip();
  }
  EXPECT_GT(book_.level_count(Side::Buy) + book_.level_count(Side::Sell), 100u);
}

}  // namespace
}  // namespace mf
