#include "fixtures.hpp"

#include <gtest/gtest.h>

#include <set>
#include <string>

namespace mf::bench {
namespace {

struct Case {
  Scenario scenario;
  int param;
};

std::string case_name(const ::testing::TestParamInfo<Case>& info) {
  return std::string(scenario_name(info.param.scenario)) + "_" + std::to_string(info.param.param);
}

template <class T>
std::size_t count(const std::vector<Event>& events) {
  std::size_t n = 0;
  for (const auto& e : events) n += std::holds_alternative<T>(e);
  return n;
}

class FixtureTest : public ::testing::TestWithParam<Case> {};

INSTANTIATE_TEST_SUITE_P(
    Scenarios, FixtureTest,
    ::testing::Values(Case{Scenario::AddExistingLevel, 1}, Case{Scenario::AddNewLevel, 1},
                      Case{Scenario::CancelKeepsLevel, 1}, Case{Scenario::CancelRemovesLevel, 1},
                      Case{Scenario::ReplaceQtyDown, 1}, Case{Scenario::ReplaceReprice, 1},
                      Case{Scenario::MatchOneLevel, 1}, Case{Scenario::MatchOneLevel, 8},
                      Case{Scenario::MatchMultiLevel, 2}, Case{Scenario::MatchMultiLevel, 32},
                      Case{Scenario::RiskCheck, 1}, Case{Scenario::RiskCheckNoLimits, 1},
                      Case{Scenario::EngineAdd, 1}),
    case_name);

// After prepare + batch + restore the book has its starting shape, batch after batch.
TEST_P(FixtureTest, BookReturnsToItsShapeAfterEveryBatch) {
  MicroScenario s({.scenario = GetParam().scenario, .batch = 64, .param = GetParam().param});
  const auto start = s.shape();
  ASSERT_FALSE(start.empty());
  for (int round = 0; round < 3; ++round) {
    s.prepare();
    s.run_batch();
    s.restore();
    ASSERT_EQ(s.shape(), start) << "round " << round;
  }
}

TEST_P(FixtureTest, BatchDoesWhatItsNameSays) {
  const int k = 64;
  const int p = GetParam().param;
  MicroScenario s({.scenario = GetParam().scenario, .batch = k, .param = p});
  const std::size_t levels_before = s.book().level_count(Side::Buy) + s.book().level_count(Side::Sell);
  s.prepare();
  const std::size_t levels_prepared = s.book().level_count(Side::Buy) + s.book().level_count(Side::Sell);
  s.run_batch();
  const std::size_t levels_after = s.book().level_count(Side::Buy) + s.book().level_count(Side::Sell);
  const auto& ev = s.batch_events();
  EXPECT_EQ(s.ops_per_batch(), k);
  EXPECT_EQ(count<Reject>(ev), 0u);
  EXPECT_EQ(s.batch_rejects(), 0u);

  switch (GetParam().scenario) {
    case Scenario::AddExistingLevel:
    case Scenario::EngineAdd:
      EXPECT_EQ(ev.size(), static_cast<std::size_t>(k));
      EXPECT_EQ(count<Ack>(ev), static_cast<std::size_t>(k));
      EXPECT_EQ(levels_after, levels_before);
      break;
    case Scenario::AddNewLevel:
      EXPECT_EQ(count<Ack>(ev), static_cast<std::size_t>(k));
      EXPECT_EQ(levels_after, levels_before + k);
      break;
    case Scenario::CancelKeepsLevel:
      EXPECT_EQ(count<Cancelled>(ev), static_cast<std::size_t>(k));
      EXPECT_EQ(ev.size(), static_cast<std::size_t>(k));
      EXPECT_EQ(levels_after, levels_prepared);
      break;
    case Scenario::CancelRemovesLevel:
      EXPECT_EQ(count<Cancelled>(ev), static_cast<std::size_t>(k));
      EXPECT_EQ(levels_prepared, levels_before + k);
      EXPECT_EQ(levels_after, levels_before);
      break;
    case Scenario::ReplaceQtyDown:
    case Scenario::ReplaceReprice: {
      const bool kept = GetParam().scenario == Scenario::ReplaceQtyDown;
      EXPECT_EQ(ev.size(), static_cast<std::size_t>(k));
      for (const auto& e : ev) {
        const auto* r = std::get_if<Replaced>(&e);
        ASSERT_NE(r, nullptr);
        EXPECT_EQ(r->priority_kept, kept);
      }
      EXPECT_EQ(levels_after, levels_prepared);
      break;
    }
    case Scenario::MatchOneLevel:
    case Scenario::MatchMultiLevel: {
      EXPECT_EQ(count<Ack>(ev), static_cast<std::size_t>(k));
      EXPECT_EQ(count<Trade>(ev), static_cast<std::size_t>(k * p));
      EXPECT_EQ(count<Fill>(ev), static_cast<std::size_t>(2 * k * p));
      EXPECT_EQ(count<Cancelled>(ev), 0u);  // every taker fills completely
      std::set<Price> prices;
      for (const auto& e : ev) {
        if (const auto* t = std::get_if<Trade>(&e)) prices.insert(t->price);
      }
      const bool multi = GetParam().scenario == Scenario::MatchMultiLevel;
      EXPECT_EQ(prices.size(), multi ? static_cast<std::size_t>(k * p) : 1u);
      EXPECT_EQ(levels_after, levels_before);  // every maker consumed
      break;
    }
    case Scenario::RiskCheck:
    case Scenario::RiskCheckNoLimits:
      EXPECT_TRUE(ev.empty());
      EXPECT_EQ(levels_after, levels_before);
      break;
  }
}

TEST(Fixtures, NamesAreDistinct) {
  std::set<std::string> names;
  for (int i = 0; i <= static_cast<int>(Scenario::EngineAdd); ++i) {
    names.insert(std::string(scenario_name(static_cast<Scenario>(i))));
  }
  EXPECT_EQ(names.size(), static_cast<std::size_t>(Scenario::EngineAdd) + 1);
}

}  // namespace
}  // namespace mf::bench
