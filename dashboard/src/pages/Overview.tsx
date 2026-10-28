// Overview (M11, M11b): a poster. One large number, the engine's throughput, then a short
// numbered list: engine latency, correctness, the honest backtest result, the architecture.
// Every number comes from a results file (virtual:facts, see facts.config.ts).
import { Link } from "react-router-dom";
import facts from "virtual:facts";

import { REPO_URL } from "../lib/links";
import { Architecture } from "../components/Architecture";
import { Term } from "../components/Term";
import { overview as C, site } from "../copy";
import { pct } from "../lib/format";
import { formatInt } from "../lib/money";
import { DSR_THRESHOLD } from "../lib/runSummary";

const ns = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(2)} µs` : `${n} ns`);

function Missing({ make }: { make: string }) {
  return <p className="muted">{C.missing(make)}</p>;
}

function Row({ id, n, title, children, narrow }: {
  id: string; n: string; title: React.ReactNode; children: React.ReactNode; narrow?: boolean;
}) {
  return (
    <section id={id} className="row" aria-labelledby={`${id}-h`}>
      <h2 className="rh" id={`${id}-h`}><span className="sn">{n}</span>{title}</h2>
      <div className={narrow ? "rb narrow" : "rb"}>{children}</div>
    </section>
  );
}

export function Overview() {
  const { bench, tests, difftest, showcase, universe } = facts;
  const ts = showcase.tsmom;
  const top = bench?.profiles[0];
  return (
    <div className="page ov">
      <div className="g12">
        <h1 className="sr-only">{site.name}</h1>
        <p className="ov-intro">{C.intro}</p>
        <div className="ov-cta">
          <Link className="arrow-link" to="/market" data-testid="watch-market">{C.ctaMarket}</Link>
          <Link className="arrow-link" to="/backtests">{C.ctaBacktests}</Link>
          <a className="arrow-link" href={REPO_URL}>{C.ctaCode}</a>
        </div>
        {bench && top && (
          <p className="hero" data-testid="hero">
            <span className="hero-n">{(top.commandsPerSec / 1e6).toFixed(1)}M</span>
            <span className="hero-u">{C.heroUnit}</span>
            <span className="hero-l">{C.heroLabel(top.name, bench.machine.cpu, bench.machine.cores)}</span>
          </p>
        )}

        <div className="ov-list">
          <Row id="perf" n="01" title={C.sections.perf}>
            {!bench ? <Missing make={C.make.bench} /> : (
              <>
                <table className="data perf">
                  <caption className="sr-only">{C.perfCaption}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{C.perfCols.workload}</th>
                      <th scope="col" className="num">{C.perfCols.rate}</th>
                      <th scope="col" className="num"><Term k="latency">{C.perfCols.p50}</Term></th>
                      <th scope="col" className="num">{C.perfCols.p99}</th>
                      <th scope="col" className="num p999">{C.perfCols.p999}</th>
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
                  {C.perfNote(bench.settings.reps, formatInt(bench.settings.n), bench.settings.seed, bench.machine.cpu,
                              bench.build.compiler,
                              bench.commit.slice(0, 7), bench.clockStepNs)}
                </p>
              </>
            )}
          </Row>

          <Row id="tests" n="02" title={C.sections.tests} narrow>
            <dl className="facts" data-testid="correctness">
              {tests ? Object.entries(tests.suites).map(([name, s]) => (
                <div key={name}><dt>{name}</dt><dd className="num hi">{C.passed(formatInt(s.passed))}</dd></div>
              )) : <div><dt>{C.testSuites}</dt><dd><Missing make={C.make.facts} /></dd></div>}
              <div>
                <dt><Term k="difftest">{C.difftest}</Term></dt>
                <dd className="num">
                  {difftest ? Object.entries(difftest.runs).map(([p, r]) => `${formatInt(r.commands)} ${p}`).join(" + ") : "–"}
                </dd>
              </div>
              <div>
                <dt>{C.result}</dt>
                <dd data-testid="difftest-result" className="hi">
                  {difftest ? (Object.values(difftest.runs).every((r) => r.result === "identical") ? C.difftestOk : C.difftestBad)
                    : <Missing make={C.make.difftest} />}
                </dd>
              </div>
              <div><dt>{C.sanitizers}</dt><dd>{C.sanitizersValue}</dd></div>
            </dl>
            <p className="foot">{C.testsNote(tests?.date ?? null)}</p>
          </Row>

          <Row id="showcase" n="03" title={C.sections.showcase} narrow>
            {!ts ? <Missing make={C.make.showcase} /> : (
              <>
                <p className="verdict" data-testid="showcase-verdict">
                  {C.verdict(ts.dsr.net >= DSR_THRESHOLD, ts.dsr.net.toFixed(2), ts.trials.backtests, DSR_THRESHOLD)}
                </p>
                <table className="data">
                  <caption className="sr-only">{C.showcaseCaption}</caption>
                  <thead><tr><th scope="col"><span className="sr-only">{C.metric}</span></th>
                    <th scope="col" className="num">{C.gross}</th><th scope="col" className="num">{C.net}</th></tr></thead>
                  <tbody>
                    <tr><th scope="row"><Term k="sharpe">{C.sharpe}</Term></th>
                      <td className="num">{ts.sharpe.gross.toFixed(2)}</td><td className="num hi">{ts.sharpe.net.toFixed(2)}</td></tr>
                    <tr><th scope="row">{C.annualReturn}</th><td className="num">{pct(ts.annual_return.gross)}</td>
                      <td className="num">{pct(ts.annual_return.net)}</td></tr>
                    <tr><th scope="row">{C.maxDrawdown}</th><td className="num">{pct(ts.max_drawdown.gross, 1)}</td>
                      <td className="num">{pct(ts.max_drawdown.net, 1)}</td></tr>
                  </tbody>
                </table>
                <p className="foot">
                  {C.showcaseNote(String(universe?.size ?? "–"), ts.period.start.slice(0, 4), ts.period.end.slice(0, 4),
                                  pct(ts.annual_cost))}{" "}
                  <Link to="/backtests/showcase-tsmom">{C.fullResult}</Link>
                </p>
              </>
            )}
          </Row>

          <Row id="architecture" n="04" title={C.sections.arch}>
            <Architecture />
            <p className="foot"><Link to="/how-it-works">{C.howLink}</Link></p>
          </Row>
        </div>
      </div>
    </div>
  );
}
