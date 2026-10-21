#include "runner.hpp"

#include <atomic>
#include <stdexcept>

namespace mf::bench {

namespace {

constexpr auto kNoHook = [](auto&&...) {};

void check_warmup(const Workload& w, std::size_t warmup) {
  if (warmup >= w.commands.size()) {
    throw std::invalid_argument("warm-up must leave commands to time");
  }
}

// Keeps the compiler from moving work across a clock read.
void fence() { std::atomic_signal_fence(std::memory_order_seq_cst); }

}  // namespace

ThroughputRep run_throughput(const Workload& w, std::size_t warmup) {
  check_warmup(w, warmup);
  Engine engine;
  std::vector<Event> events;
  events.reserve(4096);
  drive(engine, w, events, kNoHook, kNoHook, 0, warmup);

  fence();
  const std::int64_t start = now_ns();
  fence();
  const std::uint64_t n_events = drive(engine, w, events, kNoHook, kNoHook, warmup);
  fence();
  const std::int64_t end = now_ns();
  fence();

  ThroughputRep rep{.seconds = static_cast<double>(end - start) * 1e-9,
                    .commands = w.commands.size() - warmup, .events = n_events};
  rep.commands_per_sec = static_cast<double>(rep.commands) / rep.seconds;
  rep.events_per_sec = static_cast<double>(rep.events) / rep.seconds;
  return rep;
}

LatencyRep run_latency(const Workload& w, std::size_t warmup, Histogram& histogram) {
  check_warmup(w, warmup);
  Engine engine;
  std::vector<Event> events;
  events.reserve(4096);
  drive(engine, w, events, kNoHook, kNoHook, 0, warmup);

  std::int64_t start = 0;
  const std::uint64_t n_events = drive(
      engine, w, events,
      [&](std::size_t) {
        fence();
        start = now_ns();
        fence();
      },
      [&](std::size_t, const std::vector<Event>&) {
        fence();
        histogram.record(now_ns() - start);
      },
      warmup);
  return {.recorded = w.commands.size() - warmup, .events = n_events};
}

TimerOverhead measure_timer(std::size_t samples) {
  Histogram h;
  std::size_t zeros = 0;
  std::int64_t min_step = 0;
  for (std::size_t i = 0; i < samples; ++i) {
    fence();
    const std::int64_t a = now_ns();
    fence();
    const std::int64_t b = now_ns();
    fence();
    const std::int64_t d = b - a;
    h.record(d);
    if (d == 0) {
      ++zeros;
    } else if (min_step == 0 || d < min_step) {
      min_step = d;
    }
  }
  std::int64_t sink = 0;
  const std::int64_t start = now_ns();
  for (std::size_t i = 0; i < samples; ++i) sink ^= now_ns();
  const std::int64_t end = now_ns();
  [[maybe_unused]] static volatile std::int64_t keep;
  keep = sink;
  return {.p50_ns = h.percentile(50), .p99_ns = h.percentile(99), .max_ns = h.max(),
          .min_step_ns = min_step,
          .zero_fraction = static_cast<double>(zeros) / static_cast<double>(samples),
          .cost_ns = static_cast<double>(end - start) / static_cast<double>(samples)};
}

double calibrate() {
  constexpr std::uint64_t kIterations = 20'000'000;
  double best = 0;
  for (int attempt = 0; attempt < 3; ++attempt) {
    std::uint64_t x = 0x9e3779b97f4a7c15ULL + static_cast<std::uint64_t>(attempt);
    const std::int64_t start = now_ns();
    for (std::uint64_t i = 0; i < kIterations; ++i) {
      x = x * 6364136223846793005ULL + 1442695040888963407ULL;  // a dependent chain
      x ^= x >> 29;
    }
    const std::int64_t end = now_ns();
    [[maybe_unused]] static volatile std::uint64_t keep;
    keep = x;
    const double ns = static_cast<double>(end - start) / kIterations;
    if (attempt == 0 || ns < best) best = ns;
  }
  return best;
}

}  // namespace mf::bench
