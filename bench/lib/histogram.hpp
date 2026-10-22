#pragma once

#include <cstdint>

struct hdr_histogram;

namespace mf::bench {

// Latency histogram in nanoseconds: HdrHistogram with 3 significant figures, so a
// percentile is within 0.1% of the recorded value. Min and max are tracked exactly
// (hdr_max reports the top of the max's bucket, not the value itself).
class Histogram {
 public:
  static constexpr std::int64_t kHighestNs = 10'000'000'000;  // 10 s; larger values clamp

  Histogram();
  ~Histogram();
  Histogram(const Histogram&) = delete;
  Histogram& operator=(const Histogram&) = delete;

  void record(std::int64_t ns);
  // Adds every value recorded in `other`.
  void merge(const Histogram& other);
  void reset();

  std::int64_t count() const;
  // Percentile in (0, 100]; 0 when empty.
  std::int64_t percentile(double p) const;
  std::int64_t min() const;  // 0 when empty
  std::int64_t max() const;
  double mean() const;

 private:
  hdr_histogram* h_{};
  std::int64_t min_{};
  std::int64_t max_{};
};

}  // namespace mf::bench
