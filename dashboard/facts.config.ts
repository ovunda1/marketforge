// The Overview's numbers (M11), read at build time from the results files the rest of the
// repository writes: bench/results/replay.json (make bench), results/tests.json (make
// facts), results/difftest.json (make difftest-long), research/showcase/*/metrics.json
// (make showcase) and the Tiingo manifest. Exposed as the virtual module "virtual:facts"
// with only the fields the page shows; nothing is typed in by hand. A missing optional
// file becomes null and the page says how to produce it.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Plugin } from "vite";

const ROOT = resolve(__dirname, "..");
export const FACT_FILES = ["bench/results/replay.json", "results/tests.json", "results/difftest.json",
                           "research/showcase/tsmom/metrics.json", "research/showcase/xsmom/metrics.json",
                           "research/data/manifests/tiingo-etf-daily.json"];

function read(rel: string): any {
  const p = resolve(ROOT, rel);
  return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")) : null;
}

interface GrossNet { gross: number; net: number }
interface Count { tests: number; passed: number; failures: number; errors: number; skipped: number }
interface Showcase { sharpe: GrossNet; annual_return: GrossNet; max_drawdown: GrossNet; dsr: GrossNet;
                     trials: { backtests: number; dsr_trials: number }; period: { start: string; end: string };
                     annual_cost: number | null }
export interface Facts {
  bench: { machine: { cpu: string; os: string; cores: string }; build: { compiler: string; flags: string; lto: boolean };
           settings: { seed: number; n: number; reps: number }; commit: string; clockStepNs: number;
           profiles: { name: string; commandsPerSec: number; p50: number; p99: number; p999: number }[] } | null;
  tests: { total: Count; suites: Record<string, Count>; date: string } | null;
  difftest: { runs: Record<string, { seed: number; commands: number; output_lines: number; result: string;
                                     base_commit: string | null; dirty: boolean }> } | null;
  showcase: { tsmom: Showcase | null; xsmom: Showcase | null };
  universe: { size: number; start: string; end: string } | null;
}

export function buildFacts(): Facts {
  const replay = read("bench/results/replay.json");
  const tests = read("results/tests.json");
  const difftest = read("results/difftest.json");
  const manifest = read("research/data/manifests/tiingo-etf-daily.json");
  const showcase = (name: string) => {
    const m = read(`research/showcase/${name}/metrics.json`);
    if (!m) return null;
    const p = m.performance;
    return { sharpe: p.sharpe, annual_return: p.annual_return, max_drawdown: p.max_drawdown, dsr: p.dsr,
             trials: m.trials, period: m.period, annual_cost: m.cost_drag?.annual_cost ?? null };
  };
  return {
    bench: replay && {
      machine: { cpu: replay.machine.cpu, os: replay.machine.os, cores: `${replay.machine.performance_cores}P + ${replay.machine.efficiency_cores}E` },
      build: { compiler: replay.build.compiler, flags: replay.build.cxx_flags, lto: replay.build.lto },
      settings: { seed: replay.settings.seed, n: replay.settings.n, reps: replay.settings.reps },
      commit: replay.git.commit, clockStepNs: replay.timer.min_step_ns,
      profiles: Object.entries(replay.profiles as Record<string, any>).map(([name, p]) => ({
        name, commandsPerSec: p.throughput.commands_per_sec.median, p50: p.latency.p50.median,
        p99: p.latency.p99.median, p999: p.latency.p999.median })),
    },
    tests: tests && { total: tests.total, suites: tests.suites, date: tests.date },
    difftest: difftest && { runs: difftest.runs },
    showcase: { tsmom: showcase("tsmom"), xsmom: showcase("xsmom") },
    universe: manifest && { size: manifest.universe.length, start: manifest.start, end: manifest.end },
  };
}

export function factsPlugin(): Plugin {
  const id = "virtual:facts";
  return {
    name: "mf-facts",
    resolveId: (source) => (source === id ? "\0" + id : null),
    load(source) {
      if (source !== "\0" + id) return null;
      FACT_FILES.map((f) => resolve(ROOT, f)).filter(existsSync).forEach((f) => this.addWatchFile(f));
      return `export default ${JSON.stringify(buildFacts())};`;
    },
  };
}
