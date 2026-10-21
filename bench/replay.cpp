// mf-bench-replay: end-to-end throughput and per-command latency of one Engine (risk gate
// with every limit set, plus the book) on a synthetic workload, written as JSON.
//
// Usage: mf-bench-replay --profile narrow|wide [--n N] [--seed S] [--reps R]
//          [--warmup W] [--mode both|throughput|latency|digest] [--out FILE] [--journal FILE]
//          [--cooldown-ms MS] [--tolerance FRACTION] [--max-attempts K]
//
// --mode digest only generates the workload and prints its digests (and, with --journal,
// writes it as a journal for mf-replay): no measurement.
//
// Each repetition runs on a fresh Engine; the first W commands of each are processed but
// not measured. A calibration probe runs before and after every repetition; if the two
// differ by more than the tolerance, or either is slower than the session's best by
// more than it, the core changed speed (an efficiency core, throttling) and the
// repetition is repeated, up to K attempts. The last attempt is kept either way and
// marked suspect.

#include <chrono>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include "alloc_counter.hpp"
#include "build_json.hpp"
#include "histogram.hpp"
#include "mf/journal.hpp"
#include "platform.hpp"
#include "runner.hpp"
#include "workload.hpp"

namespace mf::bench {
namespace {

struct Options {
  Profile profile{Profile::Narrow};
  std::size_t n{5'000'000};
  std::uint64_t seed{20261021};
  int reps{5};
  std::size_t warmup{0};  // 0: 10% of n, at least 200k (capped below n)
  bool throughput{true};
  bool latency{true};
  std::string out;
  std::string journal;
  int cooldown_ms{500};
  double tolerance{0.05};
  int max_attempts{3};
};

[[noreturn]] void usage(const std::string& error) {
  std::cerr << "mf-bench-replay: " << error << '\n'
            << "usage: mf-bench-replay --profile narrow|wide [--n N] [--seed S] [--reps R]\n"
               "         [--warmup W] [--mode both|throughput|latency|digest] [--out FILE]\n"
               "         [--journal FILE] [--cooldown-ms MS] [--tolerance F] [--max-attempts K]\n";
  std::exit(1);
}

Options parse(int argc, char** argv) {
  Options o;
  bool have_profile = false;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (i + 1 >= argc) usage("missing value for " + arg);
    const std::string value = argv[++i];
    try {
      if (arg == "--profile") {
        const auto p = parse_profile(value);
        if (!p) usage("unknown profile " + value);
        o.profile = *p;
        have_profile = true;
      } else if (arg == "--n") {
        o.n = std::stoull(value);
      } else if (arg == "--seed") {
        o.seed = std::stoull(value);
      } else if (arg == "--reps") {
        o.reps = std::stoi(value);
      } else if (arg == "--warmup") {
        o.warmup = std::stoull(value);
      } else if (arg == "--mode") {
        if (value != "both" && value != "throughput" && value != "latency" && value != "digest") {
          usage("bad mode");
        }
        o.throughput = value == "both" || value == "throughput";
        o.latency = value == "both" || value == "latency";
      } else if (arg == "--out") {
        o.out = value;
      } else if (arg == "--journal") {
        o.journal = value;
      } else if (arg == "--cooldown-ms") {
        o.cooldown_ms = std::stoi(value);
      } else if (arg == "--tolerance") {
        o.tolerance = std::stod(value);
      } else if (arg == "--max-attempts") {
        o.max_attempts = std::stoi(value);
      } else {
        usage("unknown option " + arg);
      }
    } catch (const std::logic_error&) {
      usage("bad value for " + arg + ": " + value);
    }
  }
  if (!have_profile) usage("--profile is required");
  if (o.reps < 1 || o.max_attempts < 1 || o.n < 2) usage("reps, attempts and n must be positive");
  return o;
}

// Calibration around one repetition, and the retry rule.
class Probe {
 public:
  explicit Probe(const Options& o) : o_(o), best_(calibrate()) {}

  template <class Rep>
  Json run(Rep&& rep) {
    Json result;
    double before = 0, after = 0;
    bool suspect = true;
    int attempt = 0;
    while (suspect && attempt < o_.max_attempts) {
      ++attempt;
      std::this_thread::sleep_for(std::chrono::milliseconds(o_.cooldown_ms));
      before = calibrate();
      result = rep();
      after = calibrate();
      const double lo = std::min(before, after), hi = std::max(before, after);
      suspect = (hi - lo) / lo > o_.tolerance || hi > best_ * (1 + o_.tolerance);
      best_ = std::min(best_, lo);
    }
    result.set("calibration_before_ns", before);
    result.set("calibration_after_ns", after);
    result.set("attempts", attempt);
    result.set("suspect", suspect);
    return result;
  }

  double best() const { return best_; }

 private:
  const Options& o_;
  double best_;
};

Json percentiles_json(const Histogram& h) {
  Json j = Json::object();
  j.set("count", h.count());
  j.set("p50", h.percentile(50));
  j.set("p90", h.percentile(90));
  j.set("p99", h.percentile(99));
  j.set("p999", h.percentile(99.9));
  j.set("max", h.max());
  j.set("min", h.min());
  j.set("mean", h.mean());
  return j;
}

int run(const Options& o) {
  Json doc = Json::object();
  doc.set("tool", "mf-bench-replay");
  doc.set("build", build_json());
  doc.set("scheduling", prefer_performance_cores());

  const TimerOverhead t = measure_timer(200'000);
  Json timer = Json::object();
  timer.set("clock", "std::chrono::steady_clock");
  timer.set("p50_ns", t.p50_ns);
  timer.set("p99_ns", t.p99_ns);
  timer.set("max_ns", t.max_ns);
  timer.set("min_step_ns", t.min_step_ns);
  timer.set("zero_fraction", t.zero_fraction);
  timer.set("cost_ns", t.cost_ns);
  doc.set("timer", timer);

  const std::int64_t gen_start = now_ns();
  const Workload w = generate_workload(o.profile, o.seed, o.n);
  const double gen_seconds = static_cast<double>(now_ns() - gen_start) * 1e-9;
  const std::size_t total = w.commands.size();
  std::size_t warmup = o.warmup != 0 ? o.warmup : std::max<std::size_t>(total / 10, 200'000);
  warmup = std::min(warmup, total / 2);

  Json settings = Json::object();
  settings.set("profile", std::string(profile_name(o.profile)));
  settings.set("seed", o.seed);
  settings.set("n", static_cast<std::uint64_t>(o.n));
  settings.set("commands_total", static_cast<std::uint64_t>(total));
  settings.set("warmup", static_cast<std::uint64_t>(warmup));
  settings.set("reps", o.reps);
  settings.set("generator_version", kGeneratorVersion);
  settings.set("cooldown_ms", o.cooldown_ms);
  settings.set("tolerance", o.tolerance);
  settings.set("max_attempts", o.max_attempts);
  doc.set("settings", settings);

  Json stats = Json::object();
  stats.set("new_passive", w.stats.new_passive);
  stats.set("new_aggressive", w.stats.new_aggressive);
  stats.set("new_ioc", w.stats.new_ioc);
  stats.set("new_market", w.stats.new_market);
  stats.set("cancels", w.stats.cancels);
  stats.set("cancels_of_resting", w.stats.cancels_of_resting);
  stats.set("replaces", w.stats.replaces);
  stats.set("max_resting", w.stats.max_resting);
  stats.set("min_price", w.stats.min_price);
  stats.set("max_price", w.stats.max_price);
  stats.set("generation_seconds", gen_seconds);
  doc.set("workload", stats);

  Json digest = Json::object();
  digest.set("commands", command_digest(w));
  digest.set("events", event_digest(w));
  doc.set("digest", digest);

  if (!o.journal.empty()) {
    JournalWriter writer(o.journal);
    for (const auto& c : w.commands) {
      writer.append(JournalRecord{.timestamp = c.timestamp, .command = c.command});
    }
  }

  // Allocations per measured command, in an untimed pass.
  {
    Engine engine;
    std::vector<Event> events;
    events.reserve(4096);
    const auto noop = [](auto&&...) {};
    drive(engine, w, events, noop, noop, 0, warmup);
    const std::uint64_t before = allocation_count();
    drive(engine, w, events, noop, noop, warmup);
    doc.set("allocations_per_command",
            static_cast<double>(allocation_count() - before) / static_cast<double>(total - warmup));
  }

  const auto write = [&] {
    const std::string text = doc.dump();
    if (o.out.empty()) {
      std::cout << text;
    } else {
      std::ofstream(o.out) << text;
    }
  };
  if (!o.throughput && !o.latency) {
    write();
    return 0;
  }

  Probe probe(o);
  std::vector<double> cps, eps;
  if (o.throughput) {
    Json reps = Json::array();
    for (int r = 0; r < o.reps; ++r) {
      ThroughputRep tr;  // the kept attempt
      Json rep = probe.run([&] {
        tr = run_throughput(w, warmup);
        Json j = Json::object();
        j.set("seconds", tr.seconds);
        j.set("commands", tr.commands);
        j.set("events", tr.events);
        j.set("commands_per_sec", tr.commands_per_sec);
        j.set("events_per_sec", tr.events_per_sec);
        return j;
      });
      cps.push_back(tr.commands_per_sec);
      eps.push_back(tr.events_per_sec);
      reps.push(rep);
    }
    Json tp = Json::object();
    tp.set("reps", reps);
    tp.set("commands_per_sec", summary_json(summarize(cps)));
    tp.set("events_per_sec", summary_json(summarize(eps)));
    doc.set("throughput", tp);
  }

  if (o.latency) {
    Json reps = Json::array();
    Histogram merged;
    std::vector<double> p50, p90, p99, p999, mx, mean;
    for (int r = 0; r < o.reps; ++r) {
      Histogram h;
      Json rep = probe.run([&] {
        h.reset();
        run_latency(w, warmup, h);
        return percentiles_json(h);
      });
      merged.merge(h);
      p50.push_back(static_cast<double>(h.percentile(50)));
      p90.push_back(static_cast<double>(h.percentile(90)));
      p99.push_back(static_cast<double>(h.percentile(99)));
      p999.push_back(static_cast<double>(h.percentile(99.9)));
      mx.push_back(static_cast<double>(h.max()));
      mean.push_back(h.mean());
      reps.push(rep);
    }
    Json lat = Json::object();
    lat.set("unit", "ns");
    lat.set("reps", reps);
    lat.set("p50", summary_json(summarize(p50)));
    lat.set("p90", summary_json(summarize(p90)));
    lat.set("p99", summary_json(summarize(p99)));
    lat.set("p999", summary_json(summarize(p999)));
    lat.set("max", summary_json(summarize(mx)));
    lat.set("mean", summary_json(summarize(mean)));
    lat.set("merged", percentiles_json(merged));
    doc.set("latency", lat);

    // Per-command timing adds about one clock read to each sample; batch timing does
    // not. After removing that, the two means should roughly agree.
    if (o.throughput) {
      Json cc = Json::object();
      const double batch_ns = 1e9 / summarize(cps).median;
      const double hist_ns = summarize(mean).median;
      cc.set("batch_mean_ns", batch_ns);
      cc.set("histogram_mean_ns", hist_ns);
      cc.set("timer_cost_ns", t.cost_ns);
      const double corrected = hist_ns - t.cost_ns;
      cc.set("relative_difference", (corrected - batch_ns) / batch_ns);
      doc.set("cross_check", cc);
    }
  }
  Json calibration = Json::object();
  calibration.set("session_best_ns", probe.best());
  doc.set("calibration", calibration);

  write();
  return 0;
}

}  // namespace
}  // namespace mf::bench

int main(int argc, char** argv) {
  try {
    return mf::bench::run(mf::bench::parse(argc, argv));
  } catch (const std::exception& e) {
    std::cerr << "mf-bench-replay: " << e.what() << '\n';
    return 2;
  }
}
