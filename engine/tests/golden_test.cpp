#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "mf/text_format.hpp"

// Golden scenarios (engine/tests/golden/*.cmds) with hand-derived expected output
// (*.events). tests/test_golden.py runs the same files through the Python reference, so
// both engines are checked against independently worked answers, not just each other.
// Risk-gate scenarios (golden/risk/, M5) run against the C++ engine only: the Python
// reference is book-only.

namespace mf {
namespace {

namespace fs = std::filesystem;

std::vector<std::string> golden_names(const fs::path& dir = MF_GOLDEN_DIR) {
  std::vector<std::string> names;
  for (const auto& entry : fs::directory_iterator(dir)) {
    if (entry.path().extension() == ".cmds") names.push_back(entry.path().stem().string());
  }
  std::sort(names.begin(), names.end());
  return names;
}

std::vector<std::string> lines_of(const std::string& text) {
  std::vector<std::string> lines;
  std::istringstream in(text);
  for (std::string line; std::getline(in, line);) lines.push_back(line);
  return lines;
}

void expect_golden(const fs::path& dir, const std::string& name) {
  std::ifstream cmds(dir / (name + ".cmds"));
  std::ifstream expected_file(dir / (name + ".events"));
  ASSERT_TRUE(cmds && expected_file) << "missing golden files for " << name;
  std::stringstream expected_text;
  expected_text << expected_file.rdbuf();

  std::ostringstream actual_text;
  run_commands(cmds, actual_text);

  const auto expected = lines_of(expected_text.str());
  const auto actual = lines_of(actual_text.str());
  const std::size_t n = std::max(expected.size(), actual.size());
  for (std::size_t i = 0; i < n; ++i) {
    const std::string e = i < expected.size() ? expected[i] : "<missing>";
    const std::string a = i < actual.size() ? actual[i] : "<missing>";
    ASSERT_EQ(a, e) << "first difference at output line " << i + 1;
  }
}

class Golden : public ::testing::TestWithParam<std::string> {};
class RiskGolden : public ::testing::TestWithParam<std::string> {};

TEST_P(Golden, MatchesExpectedEvents) { expect_golden(MF_GOLDEN_DIR, GetParam()); }
TEST_P(RiskGolden, MatchesExpectedEvents) { expect_golden(MF_RISK_GOLDEN_DIR, GetParam()); }

TEST(GoldenFiles, AreDiscovered) {
  EXPECT_GE(golden_names().size(), 12u);
  EXPECT_GE(golden_names(MF_RISK_GOLDEN_DIR).size(), 7u);
}

INSTANTIATE_TEST_SUITE_P(Scenarios, Golden, ::testing::ValuesIn(golden_names()),
                         [](const auto& info) { return info.param; });
INSTANTIATE_TEST_SUITE_P(Scenarios, RiskGolden,
                         ::testing::ValuesIn(golden_names(MF_RISK_GOLDEN_DIR)),
                         [](const auto& info) { return info.param; });

}  // namespace
}  // namespace mf
