#include "runner.hpp"

#include <gtest/gtest.h>

#include <stdexcept>
#include <string>

#include "mf/checksum.hpp"
#include "mf/text_format.hpp"

namespace mf::bench {
namespace {

// The timed loop processes exactly the commands whose output the digest checks.
TEST(Runner, DriveProducesTheCheckedEventStream) {
  for (const Profile p : {Profile::Narrow, Profile::Wide}) {
    const auto w = generate_workload(p, 5, 20'000);
    Engine engine;
    std::vector<Event> events;
    Sha256 hash;
    std::string text;
    drive(engine, w, events, [](std::size_t) {},
          [&](std::size_t i, const std::vector<Event>& ev) {
            text.clear();
            append_command_output(ev, seq_no_of(w.commands[i].command), engine.book(), text);
            hash.update(text);
          });
    hash.update(format_depth(engine.book()));
    EXPECT_EQ(Sha256::hex(hash.finish()), event_digest(w)) << profile_name(p);
  }
}

std::uint64_t events_from(const Workload& w, std::size_t first) {
  Engine engine;
  std::vector<Event> events;
  std::uint64_t n = 0;
  drive(engine, w, events, [](std::size_t) {},
        [&](std::size_t i, const std::vector<Event>& ev) { n += i >= first ? ev.size() : 0; });
  return n;
}

TEST(Runner, ThroughputTimesEveryCommandAfterWarmup) {
  const auto w = generate_workload(Profile::Narrow, 5, 20'000);
  const auto rep = run_throughput(w, 5'000);
  EXPECT_EQ(rep.commands, w.commands.size() - 5'000);
  EXPECT_EQ(rep.events, events_from(w, 5'000));
  EXPECT_GT(rep.seconds, 0);
  EXPECT_DOUBLE_EQ(rep.commands_per_sec, rep.commands / rep.seconds);
  EXPECT_DOUBLE_EQ(rep.events_per_sec, rep.events / rep.seconds);
}

TEST(Runner, LatencyRecordsEveryCommandAfterWarmupAndNothingBefore) {
  const auto w = generate_workload(Profile::Wide, 5, 20'000);
  Histogram h;
  const auto rep = run_latency(w, 4'000, h);
  EXPECT_EQ(rep.recorded, w.commands.size() - 4'000);
  EXPECT_EQ(h.count(), static_cast<std::int64_t>(rep.recorded));
  EXPECT_EQ(rep.events, events_from(w, 4'000));
  EXPECT_LE(h.percentile(50), h.percentile(90));
  EXPECT_LE(h.percentile(90), h.percentile(99));
  EXPECT_LE(h.percentile(99), h.percentile(99.9));
  EXPECT_LE(h.percentile(99.9), h.max());
}

TEST(Runner, LatencyAddsToTheHistogramItIsGiven) {
  const auto w = generate_workload(Profile::Narrow, 5, 2'000);
  Histogram h;
  run_latency(w, 1'000, h);
  run_latency(w, 1'000, h);
  EXPECT_EQ(h.count(), 2 * static_cast<std::int64_t>(w.commands.size() - 1'000));
}

TEST(Runner, WarmupMustLeaveSomethingToTime) {
  const auto w = generate_workload(Profile::Narrow, 5, 100);
  Histogram h;
  EXPECT_THROW(run_throughput(w, w.commands.size()), std::invalid_argument);
  EXPECT_THROW(run_latency(w, w.commands.size(), h), std::invalid_argument);
}

// No speed assertions anywhere: only that the measurements are well formed.
TEST(Runner, TimerOverheadIsWellFormed) {
  const auto t = measure_timer(20'000);
  EXPECT_GT(t.min_step_ns, 0);
  EXPECT_LE(t.p50_ns, t.p99_ns);
  EXPECT_LE(t.p99_ns, t.max_ns);
  EXPECT_GE(t.zero_fraction, 0.0);
  EXPECT_LT(t.zero_fraction, 1.0);
  EXPECT_GT(t.cost_ns, 0.0);
}

TEST(Runner, CalibrationIsPositive) { EXPECT_GT(calibrate(), 0.0); }

}  // namespace
}  // namespace mf::bench
