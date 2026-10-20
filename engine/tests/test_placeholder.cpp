#include <gtest/gtest.h>

#include "mf/version.hpp"

// Placeholder for Milestone 1: proves the engine library builds, links and runs.
TEST(Scaffold, VersionIsNonEmpty) { EXPECT_FALSE(mf::version().empty()); }
