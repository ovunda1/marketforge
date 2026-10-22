#include "histogram.hpp"

#include <hdr/hdr_histogram.h>

#include <algorithm>
#include <new>

namespace mf::bench {

Histogram::Histogram() {
  if (hdr_init(1, kHighestNs, 3, &h_) != 0) throw std::bad_alloc();
}

Histogram::~Histogram() { hdr_close(h_); }

void Histogram::record(std::int64_t ns) {
  ns = std::clamp<std::int64_t>(ns, 0, kHighestNs);
  min_ = h_->total_count == 0 ? ns : std::min(min_, ns);
  max_ = h_->total_count == 0 ? ns : std::max(max_, ns);
  hdr_record_value(h_, ns);
}

void Histogram::merge(const Histogram& other) {
  if (other.count() == 0) return;
  min_ = count() == 0 ? other.min_ : std::min(min_, other.min_);
  max_ = count() == 0 ? other.max_ : std::max(max_, other.max_);
  hdr_add(h_, other.h_);
}

void Histogram::reset() {
  hdr_reset(h_);
  min_ = max_ = 0;
}

std::int64_t Histogram::count() const { return h_->total_count; }

std::int64_t Histogram::percentile(double p) const {
  return count() == 0 ? 0 : hdr_value_at_percentile(h_, p);
}

std::int64_t Histogram::min() const { return min_; }
std::int64_t Histogram::max() const { return max_; }
double Histogram::mean() const { return count() == 0 ? 0 : hdr_mean(h_); }

}  // namespace mf::bench
