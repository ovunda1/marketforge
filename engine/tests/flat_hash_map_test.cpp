#include "mf/flat_hash_map.hpp"

#include <gtest/gtest.h>

#include <cstdint>
#include <limits>
#include <random>
#include <unordered_map>
#include <vector>

namespace mf {
namespace {

using Map = FlatHashMap<std::uint64_t, std::int64_t>;
constexpr std::uint64_t kMax = std::numeric_limits<std::uint64_t>::max();

TEST(FlatHashMap, InsertFindErase) {
  Map m;
  EXPECT_TRUE(m.empty());
  EXPECT_EQ(m.find(7), nullptr);
  const auto [v, inserted] = m.try_emplace(7, 70);
  EXPECT_TRUE(inserted);
  EXPECT_EQ(*v, 70);
  EXPECT_EQ(*m.find(7), 70);
  EXPECT_TRUE(m.contains(7));
  EXPECT_EQ(m.size(), 1u);

  const auto [again, inserted_again] = m.try_emplace(7, 99);
  EXPECT_FALSE(inserted_again);
  EXPECT_EQ(*again, 70);  // existing value kept

  *m.find(7) = 71;
  EXPECT_EQ(*m.find(7), 71);
  EXPECT_TRUE(m.erase(7));
  EXPECT_FALSE(m.erase(7));
  EXPECT_FALSE(m.contains(7));
  EXPECT_TRUE(m.empty());
}

// The all-ones key marks an empty slot internally; it is still a valid key, as is 0.
TEST(FlatHashMap, ZeroAndMaxKeysAreOrdinaryKeys) {
  Map m;
  m.try_emplace(0, 1);
  m.try_emplace(kMax, 2);
  m.try_emplace(kMax - 1, 3);
  EXPECT_EQ(m.size(), 3u);
  EXPECT_EQ(*m.find(0), 1);
  EXPECT_EQ(*m.find(kMax), 2);
  EXPECT_EQ(*m.find(kMax - 1), 3);
  EXPECT_FALSE(m.try_emplace(kMax, 5).second);
  EXPECT_TRUE(m.erase(kMax));
  EXPECT_EQ(m.find(kMax), nullptr);
  EXPECT_EQ(m.size(), 2u);
  m.clear();
  EXPECT_EQ(m.find(0), nullptr);
  EXPECT_EQ(m.find(kMax - 1), nullptr);
  EXPECT_TRUE(m.empty());
}

TEST(FlatHashMap, GrowingKeepsEveryEntry) {
  Map m;
  for (std::uint64_t k = 1; k <= 100'000; ++k) m.try_emplace(k, static_cast<std::int64_t>(k) * 3);
  EXPECT_EQ(m.size(), 100'000u);
  for (std::uint64_t k = 1; k <= 100'000; ++k) ASSERT_EQ(*m.find(k), static_cast<std::int64_t>(k) * 3);
  EXPECT_EQ(m.find(100'001), nullptr);
}

// Erasing shifts later entries of a probe run back: every other key must stay findable.
TEST(FlatHashMap, EraseInsideProbeRunsKeepsTheRestFindable) {
  Map m;
  for (std::uint64_t k = 0; k < 2'000; ++k) m.try_emplace(k, static_cast<std::int64_t>(k));
  for (std::uint64_t k = 0; k < 2'000; k += 3) ASSERT_TRUE(m.erase(k));
  for (std::uint64_t k = 0; k < 2'000; ++k) {
    if (k % 3 == 0) {
      ASSERT_EQ(m.find(k), nullptr) << k;
    } else {
      ASSERT_NE(m.find(k), nullptr) << k;
      ASSERT_EQ(*m.find(k), static_cast<std::int64_t>(k));
    }
  }
}

TEST(FlatHashMap, ForEachVisitsEveryEntryOnce) {
  Map m;
  for (std::uint64_t k = 10; k < 60; ++k) m.try_emplace(k, 1);
  m.try_emplace(kMax, 1);
  std::uint64_t sum = 0, count = 0;
  m.for_each([&](std::uint64_t k, const std::int64_t& v) {
    sum += k == kMax ? 0 : k;
    count += static_cast<std::uint64_t>(v);
  });
  EXPECT_EQ(count, 51u);
  EXPECT_EQ(sum, (10u + 59u) * 50u / 2u);
}

TEST(FlatHashMap, ReserveAvoidsRehashUpToTheReservedSize) {
  Map m;
  m.reserve(1'000);
  const std::size_t capacity = m.capacity();
  EXPECT_GE(capacity, 1'000u);
  for (std::uint64_t k = 0; k < 1'000; ++k) m.try_emplace(k, 0);
  EXPECT_EQ(m.size(), 1'000u);
  EXPECT_EQ(m.capacity(), capacity);
}

TEST(FlatHashSet, InsertContainsErase) {
  FlatHashSet<std::uint64_t> s;
  EXPECT_TRUE(s.insert(5));
  EXPECT_FALSE(s.insert(5));
  EXPECT_TRUE(s.contains(5));
  EXPECT_TRUE(s.insert(kMax));
  EXPECT_EQ(s.size(), 2u);
  EXPECT_TRUE(s.erase(5));
  EXPECT_FALSE(s.contains(5));
  EXPECT_TRUE(s.contains(kMax));
}

// A seeded random mix of operations against std::unordered_map, keys from a small range
// so that inserts, hits, misses and erases inside long probe runs all happen often.
TEST(FlatHashMap, AgreesWithUnorderedMapOnARandomMix) {
  std::mt19937_64 rng(20261021);
  Map m;
  std::unordered_map<std::uint64_t, std::int64_t> ref;
  for (int i = 0; i < 1'000'000; ++i) {
    const auto r = rng() % 100;
    std::uint64_t key = rng() % 5'000;
    if (r == 0) key = kMax - rng() % 2;  // the reserved pattern and its neighbour
    if (r < 45) {
      const auto value = static_cast<std::int64_t>(rng());
      const auto [v, inserted] = m.try_emplace(key, value);
      const auto [it, ref_inserted] = ref.try_emplace(key, value);
      ASSERT_EQ(inserted, ref_inserted);
      ASSERT_EQ(*v, it->second);
    } else if (r < 80) {
      ASSERT_EQ(m.erase(key), ref.erase(key) == 1);
    } else {
      const auto* v = m.find(key);
      const auto it = ref.find(key);
      ASSERT_EQ(v != nullptr, it != ref.end());
      if (v != nullptr) ASSERT_EQ(*v, it->second);
    }
    ASSERT_EQ(m.size(), ref.size());
  }
}

}  // namespace
}  // namespace mf
