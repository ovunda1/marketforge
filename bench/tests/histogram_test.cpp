#include "histogram.hpp"

#include <gtest/gtest.h>

namespace mf::bench {
namespace {

// 3 significant figures: a percentile is within 0.1% of the true value.
void expect_close(std::int64_t actual, std::int64_t expected) {
  EXPECT_NEAR(static_cast<double>(actual), static_cast<double>(expected), expected * 0.001 + 1)
      << "expected about " << expected;
}

TEST(Histogram, EmptyReportsZeros) {
  Histogram h;
  EXPECT_EQ(h.count(), 0);
  EXPECT_EQ(h.percentile(50), 0);
  EXPECT_EQ(h.min(), 0);
  EXPECT_EQ(h.max(), 0);
}

TEST(Histogram, SingleValue) {
  Histogram h;
  h.record(1234);
  EXPECT_EQ(h.count(), 1);
  expect_close(h.percentile(50), 1234);
  expect_close(h.percentile(99.9), 1234);
  EXPECT_EQ(h.min(), 1234);
  EXPECT_EQ(h.max(), 1234);
}

TEST(Histogram, UniformPercentiles) {
  Histogram h;
  for (std::int64_t v = 1; v <= 10'000; ++v) h.record(v);
  EXPECT_EQ(h.count(), 10'000);
  expect_close(h.percentile(50), 5'000);
  expect_close(h.percentile(90), 9'000);
  expect_close(h.percentile(99), 9'900);
  expect_close(h.percentile(99.9), 9'990);
  EXPECT_EQ(h.min(), 1);
  EXPECT_EQ(h.max(), 10'000);
  EXPECT_NEAR(h.mean(), 5'000.5, 5.0);
}

TEST(Histogram, MaxIsTheRecordedValueNotItsBucketTop) {
  Histogram h;
  h.record(123'457);
  h.record(5);
  EXPECT_EQ(h.max(), 123'457);
  EXPECT_EQ(h.min(), 5);
}

TEST(Histogram, ZeroIsRecorded) {
  Histogram h;
  h.record(0);
  h.record(0);
  h.record(42);
  EXPECT_EQ(h.count(), 3);
  EXPECT_EQ(h.min(), 0);
  EXPECT_EQ(h.percentile(50), 0);
}

TEST(Histogram, ValuesAboveTheRangeClamp) {
  Histogram h;
  h.record(2 * Histogram::kHighestNs);
  EXPECT_EQ(h.count(), 1);
  EXPECT_EQ(h.max(), Histogram::kHighestNs);
}

TEST(Histogram, MergeAddsCountsAndKeepsExtremes) {
  Histogram a, b;
  for (int i = 0; i < 100; ++i) a.record(100);
  for (int i = 0; i < 300; ++i) b.record(10'000);
  b.record(7);
  a.merge(b);
  EXPECT_EQ(a.count(), 401);
  EXPECT_EQ(a.min(), 7);
  EXPECT_EQ(a.max(), 10'000);
  expect_close(a.percentile(50), 10'000);
  EXPECT_EQ(b.count(), 301);  // the source is unchanged
}

TEST(Histogram, MergeIntoEmptyAndResetClears) {
  Histogram a, b;
  b.record(50);
  a.merge(b);
  EXPECT_EQ(a.min(), 50);
  EXPECT_EQ(a.max(), 50);
  a.reset();
  EXPECT_EQ(a.count(), 0);
  EXPECT_EQ(a.max(), 0);
  a.record(9);
  EXPECT_EQ(a.min(), 9);
}

}  // namespace
}  // namespace mf::bench
