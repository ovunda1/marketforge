// mf-bench-micro: Google Benchmark microbenchmarks of the book's operations and the risk
// check (bench/lib/fixtures.hpp). Each iteration times one batch of operations with the
// benchmark clock (manual time); untimed setup and cleanup keep the book's shape fixed.
// Time per operation = reported real_time / the "batch" counter.

#include <benchmark/benchmark.h>

#include <string>

#include "build_json.hpp"
#include "fixtures.hpp"
#include "platform.hpp"
#include "runner.hpp"

namespace mf::bench {
namespace {

constexpr int kBatch = 256;

void run_scenario(benchmark::State& state, Scenario scenario, int param) {
  MicroScenario s({.scenario = scenario, .batch = kBatch, .param = param});
  for (auto _ : state) {
    s.prepare();
    const std::int64_t start = now_ns();
    s.run_batch();
    const std::int64_t end = now_ns();
    benchmark::DoNotOptimize(s.batch_events().data());
    s.restore();
    state.SetIterationTime(static_cast<double>(end - start) * 1e-9);
  }
  state.counters["batch"] = kBatch;
  state.SetItemsProcessed(state.iterations() * kBatch);
}

void register_all() {
  struct Entry {
    Scenario scenario;
    int param;
    bool show_param;
  };
  const Entry entries[] = {
      {Scenario::AddExistingLevel, 1, false}, {Scenario::AddNewLevel, 1, false},
      {Scenario::CancelKeepsLevel, 1, false}, {Scenario::CancelRemovesLevel, 1, false},
      {Scenario::ReplaceQtyDown, 1, false},   {Scenario::ReplaceReprice, 1, false},
      {Scenario::MatchOneLevel, 1, true},     {Scenario::MatchOneLevel, 8, true},
      {Scenario::MatchMultiLevel, 2, true},   {Scenario::MatchMultiLevel, 8, true},
      {Scenario::MatchMultiLevel, 32, true},  {Scenario::RiskCheck, 1, false},
      {Scenario::RiskCheckNoLimits, 1, false}, {Scenario::EngineAdd, 1, false},
  };
  for (const Entry& e : entries) {
    std::string name(scenario_name(e.scenario));
    if (e.show_param) name += "/" + std::to_string(e.param);
    benchmark::RegisterBenchmark(name, [e](benchmark::State& st) {
      run_scenario(st, e.scenario, e.param);
    })->UseManualTime()->Unit(benchmark::kNanosecond);
  }
}

}  // namespace
}  // namespace mf::bench

int main(int argc, char** argv) {
  const std::string scheduling = mf::bench::prefer_performance_cores();
  benchmark::Initialize(&argc, argv);
  if (benchmark::ReportUnrecognizedArguments(argc, argv)) return 1;
  benchmark::AddCustomContext("mf_build", mf::bench::build_json().dump());
  benchmark::AddCustomContext("mf_scheduling", scheduling);
  mf::bench::register_all();
  benchmark::RunSpecifiedBenchmarks();
  benchmark::Shutdown();
  return 0;
}
