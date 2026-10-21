#pragma once

#include <string>

#include "json.hpp"
#include "mf/order_book.hpp"
#include "mf_bench/build_info.hpp"
#include "stats.hpp"

namespace mf::bench {

// How this binary was built, for every results file.
inline Json build_json() {
  Json b = Json::object();
  b.set("compiler", std::string(build_info::kCompilerId) + " " + build_info::kCompilerVersion);
  b.set("build_type", build_info::kBuildType);
  b.set("cxx_flags", build_info::kCxxFlags);
  b.set("lto", std::string(build_info::kInterproceduralOptimization) == "ON");
  b.set("invariant_checks", kInvariantChecksEnabled);
  b.set("system", build_info::kSystem);
  return b;
}

inline Json summary_json(const Summary& s) {
  Json j = Json::object();
  j.set("n", static_cast<std::uint64_t>(s.n));
  j.set("median", s.median);
  j.set("min", s.min);
  j.set("max", s.max);
  j.set("mean", s.mean);
  j.set("stddev", s.stddev);
  j.set("cv", s.cv);
  return j;
}

}  // namespace mf::bench
