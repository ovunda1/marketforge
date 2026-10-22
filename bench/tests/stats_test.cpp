#include "stats.hpp"

#include <gtest/gtest.h>

#include <cmath>
#include <stdexcept>

namespace mf::bench {
namespace {

TEST(Stats, OddCount) {
  const Summary s = summarize({3, 1, 2});
  EXPECT_EQ(s.n, 3u);
  EXPECT_DOUBLE_EQ(s.median, 2);
  EXPECT_DOUBLE_EQ(s.min, 1);
  EXPECT_DOUBLE_EQ(s.max, 3);
  EXPECT_DOUBLE_EQ(s.mean, 2);
  EXPECT_DOUBLE_EQ(s.stddev, std::sqrt(2.0 / 3.0));
  EXPECT_DOUBLE_EQ(s.cv, std::sqrt(2.0 / 3.0) / 2);
}

TEST(Stats, EvenCountMedianIsTheMiddlePairsMean) {
  const Summary s = summarize({4, 1, 3, 2});
  EXPECT_DOUBLE_EQ(s.median, 2.5);
  EXPECT_DOUBLE_EQ(s.mean, 2.5);
}

TEST(Stats, SingleValueHasNoSpread) {
  const Summary s = summarize({7});
  EXPECT_DOUBLE_EQ(s.median, 7);
  EXPECT_DOUBLE_EQ(s.stddev, 0);
  EXPECT_DOUBLE_EQ(s.cv, 0);
}

TEST(Stats, EmptyThrows) { EXPECT_THROW(summarize({}), std::invalid_argument); }

}  // namespace
}  // namespace mf::bench
