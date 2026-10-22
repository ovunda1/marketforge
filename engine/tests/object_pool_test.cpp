#include "mf/object_pool.hpp"

#include <gtest/gtest.h>

#include <cstdint>
#include <set>
#include <vector>

namespace mf {
namespace {

struct Item {
  std::uint64_t id{};
  std::int64_t value{};
};

using Pool = ObjectPool<Item, 4>;  // tiny chunks, so growth is exercised

TEST(ObjectPool, CreatesWithArgumentsAndCounts) {
  Pool pool;
  Item* a = pool.create(Item{.id = 1, .value = -5});
  EXPECT_EQ(a->id, 1u);
  EXPECT_EQ(a->value, -5);
  EXPECT_EQ(pool.live(), 1u);
  pool.destroy(a);
  EXPECT_EQ(pool.live(), 0u);
}

TEST(ObjectPool, FreedSlotIsReusedBeforeGrowing) {
  Pool pool;
  Item* a = pool.create(Item{.id = 1});
  pool.create(Item{.id = 2});
  const std::size_t capacity = pool.capacity();
  ASSERT_NE(a, nullptr);
  ASSERT_GT(capacity, 0u);
  pool.destroy(a);
  Item* c = pool.create(Item{.id = 3});
  EXPECT_EQ(c, a);
  EXPECT_EQ(c->id, 3u);
  EXPECT_EQ(pool.capacity(), capacity);
}

TEST(ObjectPool, GrowingNeverMovesLiveObjects) {
  Pool pool;
  std::vector<Item*> items;
  for (std::uint64_t i = 0; i < 1'000; ++i) items.push_back(pool.create(Item{.id = i}));
  EXPECT_GE(pool.capacity(), 1'000u);
  for (std::uint64_t i = 0; i < items.size(); ++i) EXPECT_EQ(items[i]->id, i);
  EXPECT_EQ(std::set<Item*>(items.begin(), items.end()).size(), items.size());
}

TEST(ObjectPool, ReserveAllocatesOnceUpFront) {
  Pool pool;
  pool.reserve(100);
  const std::size_t capacity = pool.capacity();
  EXPECT_GE(capacity, 100u);
  std::vector<Item*> items;
  for (int i = 0; i < 100; ++i) items.push_back(pool.create(Item{}));
  EXPECT_EQ(pool.capacity(), capacity);
  for (Item* item : items) pool.destroy(item);
  EXPECT_EQ(pool.live(), 0u);
}

TEST(ObjectPool, ChurnKeepsCapacityAtThePeak) {
  Pool pool;
  std::vector<Item*> live;
  for (int round = 0; round < 50; ++round) {
    for (int i = 0; i < 20; ++i) live.push_back(pool.create(Item{.id = static_cast<std::uint64_t>(i)}));
    for (Item* item : live) pool.destroy(item);
    live.clear();
  }
  EXPECT_GE(pool.capacity(), 20u);
  EXPECT_LE(pool.capacity(), 20u + 4u);  // never more than the peak plus one chunk
}

// Under AddressSanitizer a freed slot is poisoned: touching it is reported.
TEST(ObjectPoolDeathTest, UseAfterDestroyIsCaughtUnderAsan) {
  if (!kPoolPoisoning) GTEST_SKIP() << "needs an AddressSanitizer build (make sanitize)";
  GTEST_FLAG_SET(death_test_style, "threadsafe");
  EXPECT_DEATH(
      {
        Pool pool;
        Item* a = pool.create(Item{.id = 1});
        pool.destroy(a);
        volatile std::uint64_t id = a->id;
        (void)id;
      },
      "use-after-poison");
}

}  // namespace
}  // namespace mf
