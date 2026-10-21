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

namespace mf {
namespace {

namespace fs = std::filesystem;

std::vector<std::string> golden_names() {
  std::vector<std::string> names;
  for (const auto& entry : fs::directory_iterator(MF_GOLDEN_DIR)) {
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

class Golden : public ::testing::TestWithParam<std::string> {};

TEST_P(Golden, MatchesExpectedEvents) {
  const fs::path dir = MF_GOLDEN_DIR;
  std::ifstream cmds(dir / (GetParam() + ".cmds"));
  std::ifstream expected_file(dir / (GetParam() + ".events"));
  ASSERT_TRUE(cmds && expected_file) << "missing golden files for " << GetParam();
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

TEST(GoldenFiles, AreDiscovered) { EXPECT_GE(golden_names().size(), 12u); }

INSTANTIATE_TEST_SUITE_P(Scenarios, Golden, ::testing::ValuesIn(golden_names()),
                         [](const auto& info) { return info.param; });

}  // namespace
}  // namespace mf
