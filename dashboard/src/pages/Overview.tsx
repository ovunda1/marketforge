// Overview (M11): an engineering showcase. Every number comes from a results file
// (virtual:facts, see facts.config.ts), labelled with its workload, machine and source.
import { Link } from "react-router-dom";
import facts from "virtual:facts";

import { REPO_URL } from "../App";
import { Architecture } from "../components/Architecture";
import { Info } from "../components/Info";
import { Panel } from "../components/Panel";
import { pct } from "../lib/format";
import { formatInt } from "../lib/money";
import { DSR_THRESHOLD } from "../lib/runSummary";

const ns = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(2)} µs` : `${n} ns`);

function Missing({ make }: { make: string }) {
  return <p className="muted">Not recorded yet: run <code>{make}</code>.</p>;
}

export function Overview() {
  const { bench, tests, difftest, showcase, universe } = facts;
  const ts = showcase.tsmom;
  return (
    <div className="page">
      <section className="intro">
        <h1>MarketForge</h1>
        <p>
          A price-time priority limit order book in C++20, a pre-trade risk gate, a binary journal with deterministic
          replay, Python bindings, an event-driven backtester with transaction-cost models, and FastAPI services around
          a single-writer matcher. The market on this site is simulated; its symbols are fictional.
        </p>
        <div className="cta">
          <Link className="btn primary" to="/market" data-testid="watch-market">Watch the live market →</Link>
          <Link className="btn outline" to="/backtests">See the backtests</Link>
          <a className="btn outline" href={REPO_URL}>Read the code</a>
        </div>
      </section>

      <Panel id="architecture" title="Architecture" right={<Link className="panel-link" to="/how-it-works">How it works →</Link>}>
        <div className="panel-b"><Architecture /></div>
      </Panel>

      <div className="cols">
        <Panel id="perf" title="Engine performance" right={<span className="src">bench/results/replay.json</span>}>
          <div className="panel-b">
            {!bench ? <Missing make="make bench" /> : (
              <>
                <table className="data perf">
                  <caption className="sr-only">Commands per second and per-command latency by workload</caption>
                  <thead>
                    <tr>
                      <th scope="col">Workload</th>
                      <th scope="col" className="num">Commands/s</th>
                      <th scope="col" className="num">p50</th>
                      <th scope="col" className="num">p99</th>
                      <th scope="col" className="num p999">p99.9</th>
                    </tr>
                  </thead>
                  <tbody data-testid="perf-rows">
                    {bench.profiles.map((r) => (
                      <tr key={r.name}>
                        <th scope="row">{r.name}</th>
                        <td className="num hi">{(r.commandsPerSec / 1e6).toFixed(2)} M</td>
                        <td className="num">{ns(r.p50)}</td>
                        <td className="num">{ns(r.p99)}</td>
                        <td className="num p999">{ns(r.p999)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="foot">
                  narrow: few deep levels near a fixed mid; wide: a drifting mid, orders far from it. One core, risk
                  gate and book, median of {bench.settings.reps} × {formatInt(bench.settings.n)} commands (seed{" "}
                  {bench.settings.seed}). {bench.machine.cpu} ({bench.machine.cores}), {bench.build.compiler},{" "}
                  <code>{bench.build.flags}</code>, commit {bench.commit.slice(0, 7)}. Latency is quantised to the
                  clock step ({bench.clockStepNs} ns).
                </p>
              </>
            )}
          </div>
        </Panel>

        <Panel id="tests" title="Correctness" right={<span className="src">results/*.json</span>}>
          <div className="panel-b">
            <dl className="facts" data-testid="correctness">
              {tests ? Object.entries(tests.suites).map(([name, s]) => (
                <div key={name}><dt>{name}</dt><dd className="num hi">{formatInt(s.passed)} passed</dd></div>
              )) : <div><dt>Test suites</dt><dd><Missing make="make facts" /></dd></div>}
              <div>
                <dt>Differential test <Info term="difftest" label="the differential test" /></dt>
                <dd className="mono">
                  {difftest ? Object.entries(difftest.runs).map(([p, r]) => `${formatInt(r.commands)} ${p}`).join(" + ") : "–"}
                </dd>
              </div>
              <div>
                <dt>Result</dt>
                <dd data-testid="difftest-result">
                  {difftest ? (Object.values(difftest.runs).every((r) => r.result === "identical")
                    ? "C++ and Python engines identical" : "MISMATCH: see results/difftest.json") : <Missing make="make difftest-long" />}
                </dd>
              </div>
              <div><dt>Sanitizers</dt><dd>ASan + UBSan, full suite in CI</dd></div>
            </dl>
            <p className="foot">The differential test runs random commands through the C++ engine and an independent
              Python reference engine and requires every event to match.{tests ? ` Test counts from ${tests.date} (make facts).` : ""}</p>
          </div>
        </Panel>

        <Panel id="showcase" title="Showcase backtest" right={<span className="src">research/showcase</span>}>
          <div className="panel-b">
            {!ts ? <Missing make="make showcase" /> : (
              <>
                <p className="verdict" data-testid="showcase-verdict">
                  {ts.dsr.net >= DSR_THRESHOLD ? "Statistically significant" : "Not statistically significant"}:
                  deflated Sharpe {ts.dsr.net.toFixed(2)} after {ts.trials.backtests} logged backtests (needs ≥ {DSR_THRESHOLD}).
                </p>
                <table className="data">
                  <caption className="sr-only">Time-series momentum showcase, gross and net of costs</caption>
                  <thead><tr><th scope="col"><span className="sr-only">Metric</span></th><th scope="col" className="num">Gross</th>
                    <th scope="col" className="num">Net of costs</th></tr></thead>
                  <tbody>
                    <tr><th scope="row">Sharpe ratio <Info term="sharpe" label="the Sharpe ratio" /></th>
                      <td className="num">{ts.sharpe.gross.toFixed(2)}</td><td className="num hi">{ts.sharpe.net.toFixed(2)}</td></tr>
                    <tr><th scope="row">Annual return</th><td className="num">{pct(ts.annual_return.gross)}</td>
                      <td className="num">{pct(ts.annual_return.net)}</td></tr>
                    <tr><th scope="row">Max drawdown</th><td className="num">{pct(ts.max_drawdown.gross, 1)}</td>
                      <td className="num">{pct(ts.max_drawdown.net, 1)}</td></tr>
                  </tbody>
                </table>
                <p className="foot">
                  Time-series momentum on {universe?.size ?? "–"} US ETFs (historical data, Tiingo), out-of-sample
                  walk-forward {ts.period.start.slice(0, 4)}–{ts.period.end.slice(0, 4)}. Costs:{" "}
                  {pct(ts.annual_cost)} a year. <Link to="/backtests/showcase-tsmom">Full result →</Link>
                </p>
              </>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
