#pragma once

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "histogram.hpp"
#include "mf/engine.hpp"
#include "workload.hpp"

namespace mf::bench {

// The benchmark clock: std::chrono::steady_clock, in nanoseconds. On macOS this reads
// the 24 MHz system counter (one tick = 41.67 ns); on Linux it is usually TSC-backed
// with nanosecond resolution. Its cost and step are measured by measure_timer().
inline std::int64_t now_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

// Runs commands [first, last) of `w` through `engine` in order. before(i) runs just
// before command i and after(i, events) just after it, with that command's events; the
// event buffer is then cleared. Returns the number of events. Throughput, latency and
// the tests all use this one loop, so what is timed is exactly what is checked.
template <class Before, class After>
std::uint64_t drive(Engine& engine, const Workload& w, std::vector<Event>& events,
                    Before&& before, After&& after, std::size_t first = 0,
                    std::size_t last = static_cast<std::size_t>(-1)) {
  std::uint64_t total_events = 0;
  last = std::min(last, w.commands.size());
  for (std::size_t i = first; i < last; ++i) {
    const TimedCommand& c = w.commands[i];
    before(i);
    engine.process(c.command, c.timestamp, events);
    after(i, events);
    total_events += events.size();
    events.clear();
  }
  return total_events;
}

struct ThroughputRep {
  double seconds{};          // commands [warmup, n)
  std::uint64_t commands{};  // timed commands
  std::uint64_t events{};    // events of the timed commands
  double commands_per_sec{};
  double events_per_sec{};
};

// One repetition on a fresh Engine: commands [0, warmup) run untimed, then one clock
// read before and after the rest. The clock's cost is negligible against millions of
// commands.
ThroughputRep run_throughput(const Workload& w, std::size_t warmup);

struct LatencyRep {
  std::uint64_t recorded{};  // n - warmup
  std::uint64_t events{};    // events of the recorded commands
};

// One repetition on a fresh Engine: each command from `warmup` on is timed on its own
// (one clock read before, one after) and recorded in `histogram` (not reset here).
LatencyRep run_latency(const Workload& w, std::size_t warmup, Histogram& histogram);

// The clock measured against itself: the distribution of back-to-back read deltas,
// the smallest non-zero step seen, and the mean cost of one read.
struct TimerOverhead {
  std::int64_t p50_ns{};
  std::int64_t p99_ns{};
  std::int64_t max_ns{};
  std::int64_t min_step_ns{};  // smallest non-zero delta: the clock's resolution
  double zero_fraction{};      // share of back-to-back deltas that read 0
  double cost_ns{};            // mean time per read over a long run of reads
};
TimerOverhead measure_timer(std::size_t samples);

// Nanoseconds per iteration of a fixed integer loop: a probe of the current core's
// speed. A performance core, an efficiency core and a throttled core give clearly
// different values, so a repetition whose probe moved is marked suspect.
double calibrate();

}  // namespace mf::bench
