#pragma once

#include <cstddef>
#include <vector>

namespace mf::bench {

// Spread of repeated measurements. The headline is the median; min, max and the
// coefficient of variation (stddev / mean, population stddev) show how much to trust it.
struct Summary {
  std::size_t n{};
  double median{};
  double min{};
  double max{};
  double mean{};
  double stddev{};
  double cv{};
};

// Throws std::invalid_argument on an empty input.
Summary summarize(std::vector<double> values);

}  // namespace mf::bench
