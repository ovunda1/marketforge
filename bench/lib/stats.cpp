#include "stats.hpp"

#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>

namespace mf::bench {

Summary summarize(std::vector<double> values) {
  if (values.empty()) throw std::invalid_argument("summarize: no values");
  std::sort(values.begin(), values.end());
  const std::size_t n = values.size();
  Summary s{.n = n, .min = values.front(), .max = values.back()};
  s.median = n % 2 == 1 ? values[n / 2] : (values[n / 2 - 1] + values[n / 2]) / 2;
  s.mean = std::accumulate(values.begin(), values.end(), 0.0) / static_cast<double>(n);
  double sq = 0;
  for (const double v : values) sq += (v - s.mean) * (v - s.mean);
  s.stddev = std::sqrt(sq / static_cast<double>(n));
  s.cv = s.mean == 0 ? 0 : s.stddev / s.mean;
  return s;
}

}  // namespace mf::bench
