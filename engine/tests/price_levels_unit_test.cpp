// PriceLevels (M6) on its own, against std::map as the model: a window of tick slots
// around the best price, with the levels outside it in an overflow map.

#include "mf/price_levels.hpp"

#include <gtest/gtest.h>

#include <limits>
#include <map>
#include <random>
#include <vector>

namespace mf {
namespace {

struct FakeLevel {
  Price price{};
  int tag{};
};

constexpr std::size_t kWindow = 64;  // small, so prices leave the window easily
using Bids = PriceLevels<FakeLevel, true, kWindow>;
using Asks = PriceLevels<FakeLevel, false, kWindow>;

template <class Levels>
std::vector<Price> prices(const Levels& levels) {
  std::vector<Price> out;
  for (const FakeLevel* l = levels.first(); l != nullptr; l = levels.after(*l)) out.push_back(l->price);
  return out;
}

TEST(PriceLevelsUnit, EmptyHasNoBest) {
  Bids b;
  EXPECT_TRUE(b.empty());
  EXPECT_EQ(b.first(), nullptr);
  EXPECT_EQ(b.find(100), nullptr);
  EXPECT_EQ(b.size(), 0u);
  b.check();
}

TEST(PriceLevelsUnit, GetOrCreateReturnsTheSameLevel) {
  Asks a;
  FakeLevel& l = a.get_or_create(500);
  l.tag = 7;
  EXPECT_EQ(&a.get_or_create(500), &l);
  EXPECT_EQ(a.find(500)->tag, 7);
  EXPECT_EQ(l.price, 500);
  EXPECT_EQ(a.size(), 1u);
}

TEST(PriceLevelsUnit, BidsBestIsHighestAsksBestIsLowest) {
  Bids b;
  Asks a;
  for (const Price p : {100, 5, 1'000'000, 101, 99}) {
    b.get_or_create(p);
    a.get_or_create(p);
    b.check();
    a.check();
  }
  EXPECT_EQ(prices(b), (std::vector<Price>{1'000'000, 101, 100, 99, 5}));
  EXPECT_EQ(prices(a), (std::vector<Price>{5, 99, 100, 101, 1'000'000}));
}

TEST(PriceLevelsUnit, ErasingTheBestFindsTheNextInsideAndOutsideTheWindow) {
  Bids b;
  for (const Price p : {1'000, 999, 990, 500, 10}) b.get_or_create(p);
  const std::vector<Price> expected = {1'000, 999, 990, 500, 10};
  ASSERT_EQ(b.size(), expected.size());
  for (std::size_t i = 0; i < expected.size(); ++i) {
    ASSERT_NE(b.first(), nullptr);
    ASSERT_EQ(b.first()->price, expected[i]);
    b.erase(*b.first());
    b.check();
  }
  EXPECT_TRUE(b.empty());
}

TEST(PriceLevelsUnit, LevelsKeepTheirAddressWhenTheWindowMoves) {
  Asks a;
  FakeLevel* far = &a.get_or_create(10'000);
  far->tag = 1;
  FakeLevel* near = &a.get_or_create(10'001);
  for (Price p = 9'999; p > 9'000; p -= 7) a.get_or_create(p);  // best moves down a lot
  a.get_or_create(1);                                            // and far away
  a.check();
  EXPECT_EQ(a.find(10'000), far);
  EXPECT_EQ(a.find(10'001), near);
  EXPECT_EQ(far->tag, 1);
}

TEST(PriceLevelsUnit, ExtremePrices) {
  constexpr Price kMax = std::numeric_limits<Price>::max();
  Bids b;
  Asks a;
  for (const Price p : {Price{1}, kMax, kMax - 1, Price{2}, kMax / 2}) {
    b.get_or_create(p);
    a.get_or_create(p);
    b.check();
    a.check();
  }
  EXPECT_EQ(prices(b), (std::vector<Price>{kMax, kMax - 1, kMax / 2, 2, 1}));
  EXPECT_EQ(prices(a), (std::vector<Price>{1, 2, kMax / 2, kMax - 1, kMax}));
  b.erase(*b.find(kMax));
  a.erase(*a.find(1));
  b.check();
  a.check();
  EXPECT_EQ(b.first()->price, kMax - 1);
  EXPECT_EQ(a.first()->price, 2);
}

// A seeded random mix against std::map, around a drifting centre with jumps far away,
// the full consistency check after every early operation and often after that.
template <class Levels, class Model>
void random_mix(std::uint64_t seed) {
  std::mt19937_64 rng(seed);
  Levels levels;
  Model model;
  Price centre = 1'000'000;
  for (int i = 0; i < 200'000; ++i) {
    if (rng() % 32 == 0) centre += static_cast<Price>(rng() % 101) - 50;
    Price p = centre + static_cast<Price>(rng() % 201) - 100;
    if (rng() % 50 == 0) p = centre + static_cast<Price>(rng() % 200'001) - 100'000;
    const auto r = rng() % 10;
    if (r < 5) {
      FakeLevel& l = levels.get_or_create(p);
      if (model.emplace(p, static_cast<int>(i)).second) l.tag = static_cast<int>(i);
      ASSERT_EQ(l.tag, model.at(p));
    } else if (r < 8) {
      FakeLevel* l = levels.find(p);
      ASSERT_EQ(l != nullptr, model.contains(p)) << p;
      if (l != nullptr) {
        levels.erase(*l);
        model.erase(p);
      }
    } else if (!model.empty()) {
      ASSERT_EQ(levels.first()->price, model.begin()->first);
      levels.erase(*levels.first());
      model.erase(model.begin());
    }
    ASSERT_EQ(levels.size(), model.size());
    if (i < 5'000 || i % 97 == 0) levels.check();
    if (i % 1'000 == 0) {
      std::vector<Price> expected;
      for (const auto& [price, tag] : model) expected.push_back(price);
      ASSERT_EQ(prices(levels), expected);
    }
  }
}

TEST(PriceLevelsUnit, BidsAgreeWithStdMap) {
  random_mix<Bids, std::map<Price, int, std::greater<>>>(1);
}

TEST(PriceLevelsUnit, AsksAgreeWithStdMap) { random_mix<Asks, std::map<Price, int>>(2); }

}  // namespace
}  // namespace mf
