#include "mf/id_set.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <random>
#include <unordered_set>
#include <vector>

namespace mf {
namespace {

constexpr OrderId kMax = std::numeric_limits<OrderId>::max();

std::vector<OrderId> sorted_ids(const IdSet& s) {
  std::vector<OrderId> ids;
  s.for_each([&](OrderId id) { ids.push_back(id); });
  std::sort(ids.begin(), ids.end());
  return ids;
}

TEST(IdSet, InsertContains) {
  IdSet s;
  EXPECT_FALSE(s.contains(5));
  EXPECT_TRUE(s.insert(5));
  EXPECT_FALSE(s.insert(5));
  EXPECT_TRUE(s.contains(5));
  EXPECT_FALSE(s.contains(4));
  EXPECT_FALSE(s.contains(6));
  EXPECT_EQ(s.size(), 1u);
}

// Bits at word and page boundaries, and the extremes of the id range.
TEST(IdSet, BoundariesAndExtremes) {
  IdSet s;
  const std::vector<OrderId> ids = {0, 63, 64, 511, 512, 513, 1'000'000, kMax - 512, kMax - 1, kMax};
  for (const OrderId id : ids) EXPECT_TRUE(s.insert(id)) << id;
  for (const OrderId id : ids) EXPECT_TRUE(s.contains(id)) << id;
  for (const OrderId id : {OrderId{1}, OrderId{62}, OrderId{510}, OrderId{514}, kMax - 2}) {
    EXPECT_FALSE(s.contains(id)) << id;
  }
  EXPECT_EQ(s.size(), ids.size());
  EXPECT_EQ(sorted_ids(s), ids);
}

TEST(IdSet, ClearForgetsEverythingAndTheSetIsReusable) {
  IdSet s;
  for (OrderId id = 1; id <= 5'000; ++id) s.insert(id);
  s.clear();
  EXPECT_EQ(s.size(), 0u);
  EXPECT_FALSE(s.contains(1));
  EXPECT_FALSE(s.contains(5'000));
  EXPECT_TRUE(sorted_ids(s).empty());
  EXPECT_TRUE(s.insert(5'000));
  EXPECT_EQ(sorted_ids(s), std::vector<OrderId>{5'000});
}

TEST(IdSet, DenseRunOfSequentialIds) {
  IdSet s;
  for (OrderId id = 1'000; id < 101'000; ++id) ASSERT_TRUE(s.insert(id));
  EXPECT_EQ(s.size(), 100'000u);
  EXPECT_FALSE(s.contains(999));
  EXPECT_FALSE(s.contains(101'000));
  const auto ids = sorted_ids(s);
  ASSERT_EQ(ids.size(), 100'000u);
  EXPECT_EQ(ids.front(), 1'000u);
  EXPECT_EQ(ids.back(), 100'999u);
}

// A seeded mix of dense, sparse and huge ids against std::unordered_set.
TEST(IdSet, AgreesWithUnorderedSet) {
  std::mt19937_64 rng(5);
  IdSet s;
  std::unordered_set<OrderId> ref;
  OrderId next = 1;
  for (int i = 0; i < 300'000; ++i) {
    OrderId id = 0;
    switch (rng() % 4) {
      case 0: id = next++; break;                          // sequential
      case 1: id = rng() % (next + 10); break;             // recent, often present
      case 2: id = rng(); break;                           // anywhere
      default: id = kMax - rng() % 2'000; break;           // near the top
    }
    if (rng() % 2 == 0) {
      ASSERT_EQ(s.insert(id), ref.insert(id).second) << id;
    } else {
      ASSERT_EQ(s.contains(id), ref.contains(id)) << id;
    }
    if (i == 150'000) {  // a session reset half way
      s.clear();
      ref.clear();
    }
  }
  EXPECT_EQ(s.size(), ref.size());
  std::vector<OrderId> expected(ref.begin(), ref.end());
  std::sort(expected.begin(), expected.end());
  EXPECT_EQ(sorted_ids(s), expected);
}

}  // namespace
}  // namespace mf
