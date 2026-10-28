// One backtest's results (M11, M11b): a plain-language first sentence (including when the result
// is not statistically significant), live progress while it runs, then the equity curve,
// drawdown, cost breakdown and the gross-vs-net table from its metrics.json.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { CostBars, LineChart } from "../components/Charts";
import { ErrorNote } from "../components/ErrorNote";
import { Panel } from "../components/Panel";
import { Term } from "../components/Term";
import { backtests, run as C, site } from "../copy";
import { api, API_BASE, wsUrl } from "../lib/api";
import { microDollars, num, pct } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { parseJson } from "../lib/json";
import { explain } from "../lib/rejects";
import { summarizeRun, type RunMetrics } from "../lib/runSummary";
import { DataChip, OverfittingNote, STRATEGY_LABEL, type RunList } from "./Backtests";

interface GN { gross: number | null; net: number | null }
interface Metrics extends RunMetrics {
  performance: Record<string, GN>;
  series?: { dates: string[]; gross_cum_return: number[]; net_cum_return: number[]; net_drawdown: number[] };
  costs?: { by_component: Record<string, number | string>; bps_of_traded_notional?: number };
  cost_drag?: { annual_cost: number; break_even_multiple: number };
  strategy?: { name: string; folds?: { params: Record<string, number> }[] };
  trials?: { backtests: number; dsr_trials: number; grid_size: number; folds: number };
  period?: { start: string; end: string; bars: number };
  data?: { name: string; survivorship?: string }[];
}
interface View {
  id: string; status: string; error: string | null; featured: boolean; created_at: string | null;
  finished_at: string | null; commit: string | null; metrics: Metrics | null; tearsheet_url: string | null;
  summary: { strategy: string | null; dataset: string; real_data: boolean; costs: boolean; start: string | null;
             end: string | null; symbols: string[] | null };
  config: { walkforward?: { train: number; test: number } } | null;
}

const STAGES = ["queued", "running", "validated", "data_loaded", "simulated", "written", "done"];

const ROWS: [string, (v: number | null) => string][] = [
  ["annual_return", (v) => pct(v)], ["volatility", (v) => pct(v)], ["sharpe", (v) => num(v)], ["sortino", (v) => num(v)],
  ["max_drawdown", (v) => pct(v, 1)], ["calmar", (v) => num(v)], ["daily_hit_rate", (v) => pct(v, 1)],
  ["total_return", (v) => pct(v)], ["dsr", (v) => num(v)],
];

export function BacktestRun() {
  const { id = "" } = useParams();
  const view = useAsync(() => api<View>(`/backtests/${encodeURIComponent(id)}`), [id]);
  const counts = useAsync(() => api<RunList>("/backtests?limit=1").then((r) => r.counts), [id]);
  const [stages, setStages] = useState<string[]>([]);
  const status = view.data?.status;

  // Live progress while queued or running; the page reloads the result when it is done.
  useEffect(() => {
    if (!status || status === "done" || status === "failed") return;
    const ws = new WebSocket(wsUrl(`/ws/backtests/${encodeURIComponent(id)}`));
    ws.onmessage = (ev) => {
      const msg = parseJson<{ type: string; stage?: string; status?: string }>(String(ev.data));
      if (msg.type === "status" && msg.status) setStages((s) => [...new Set([...s, msg.status!])]);
      if (msg.type === "progress" && msg.stage) setStages((s) => [...new Set([...s, msg.stage!])]);
      if (msg.type === "done" || msg.type === "failed") {
        setStages((s) => [...new Set([...s, msg.type])]);
        view.reload();
        counts.reload();
      }
    };
    return () => ws.close(1000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, status]);

  if (view.error) return <div className="page"><ErrorNote e={explain(view.error)} /><Link to="/backtests">{C.all}</Link></div>;
  const v = view.data;
  if (!v) return <div className="page"><p className="muted">{C.loading}</p></div>;
  const m = v.metrics;
  const s = v.summary;
  const run = m ? summarizeRun(m) : null;
  const reached = Math.max(0, ...stages.map((x) => STAGES.indexOf(x)), STAGES.indexOf(v.status));
  const totalCost = m?.costs
    ? microDollars(Object.values(m.costs.by_component).reduce<bigint>((a, x) => a + BigInt(x), 0n).toString()) : null;

  return (
    <div className="page">
      <header className="page-head run-head">
        <h1>{STRATEGY_LABEL[s.strategy ?? ""] ?? backtests.backtest}{v.featured ? C.showcase : ""}</h1>
        {run && <p className={`lead ${run.significant === false ? "not-significant" : run.significant ? "significant" : ""}`}
                   data-testid="run-summary">{run.sentence}</p>}
        <div className="meta">
          <span>{s.dataset} <DataChip real={s.real_data} /></span>
          {m?.period && <span>{C.outOfSample} <b className="num">{m.period.start} – {m.period.end}</b></span>}
          {s.symbols && <span>{s.real_data ? C.tickers : C.symbols}: <b className="num">{s.symbols.join(" ")}</b></span>}
          <span>{C.costs} <b>{s.costs ? C.on : C.off}</b></span>
          {v.config?.walkforward && <span>{C.train} <b className="num">{v.config.walkforward.train}</b> {C.days}{" "}
            <b className="num">{v.config.walkforward.test}</b></span>}
          {m?.trials && <span>{C.inRun} <b className="num">{m.trials.backtests}</b></span>}
          {v.commit && <span>{C.commit} <b className="num">{v.commit.slice(0, 7)}</b></span>}
        </div>
        <p className="advice" data-testid="not-advice">{site.notAdvice}</p>
      </header>

      {(v.status === "queued" || v.status === "running") && (
        <Panel id="progress" title={C.progress}>
          <div className="panel-b progress" role="status" aria-live="polite" data-testid="progress">
            <div className="bar-track" aria-hidden="true"><div className="bar-fill" style={{ width: `${(reached / (STAGES.length - 1)) * 100}%` }} /></div>
            <ol>{STAGES.slice(0, reached + 1).map((x) => <li key={x}>{C.stages[x] ?? x}</li>)}</ol>
          </div>
        </Panel>
      )}
      {v.status === "failed" && (
        <div className="note error" role="alert" data-testid="run-failed">
          <strong>{C.failedTitle}</strong><p>{v.error}</p>
          <p>{C.failedNote}</p>
        </div>
      )}

      <OverfittingNote counts={counts.data ?? null} trials={m?.trials?.dsr_trials} />

      {m?.series && (
        <>
          <Panel id="equity" n="01" title={<>{C.equity}<span className="sub-h">, <Term k="grossNet">{C.equitySub}</Term></span></>}>
            <div className="panel-b">
              <LineChart title={C.equityChart} dates={m.series.dates}
                         series={[{ key: "gross", label: C.gross, values: m.series.gross_cum_return },
                                  { key: "net", label: C.net, values: m.series.net_cum_return }]} />
            </div>
          </Panel>
          <Panel id="drawdown" n="02" title={<><Term k="drawdown">{C.drawdown}</Term><span className="sub-h">, {C.drawdownSub}</span></>}>
            <div className="panel-b">
              <LineChart title={C.drawdownChart} dates={m.series.dates} area
                         series={[{ key: "net", label: C.drawdownNet, values: m.series.net_drawdown.map((x) => -Math.abs(x)) }]} />
            </div>
          </Panel>
        </>
      )}

      {m && (
        <div className="halves">
          <Panel id="gross-net" n="03" title={C.grossNet}>
            <table className="data" data-testid="gross-net">
              <caption className="sr-only">{C.grossNetCaption}</caption>
              <thead><tr><th scope="col">{C.metric}</th><th scope="col" className="num">{C.grossCol}</th>
                <th scope="col" className="num">{C.netCol}</th></tr></thead>
              <tbody>
                {ROWS.filter(([k]) => m.performance[k]).map(([k, f]) => (
                  <tr key={k}><th scope="row">{k === "dsr" ? <Term k="dsr">{C.rows[k]}</Term> : C.rows[k]}</th>
                    <td className="num">{f(m.performance[k]!.gross)}</td><td className="num hi">{f(m.performance[k]!.net)}</td></tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel id="costs" n="04" title={<Term k="costs">{C.costBreakdown}</Term>}>
            <div className="panel-b">
              {m.costs ? <CostBars byComponent={m.costs.by_component} /> : <p className="muted">{C.noCosts}</p>}
              {m.cost_drag && (
                <p className="foot">{C.costDrag(pct(m.cost_drag.annual_cost),
                  m.costs?.bps_of_traded_notional != null ? num(m.costs.bps_of_traded_notional, 1) : null, totalCost)}</p>
              )}
            </div>
          </Panel>
        </div>
      )}

      <p className="links-row">
        {v.tearsheet_url && <a href={`${API_BASE}${v.tearsheet_url}`}>{C.tearsheet}</a>}
        <Link to="/backtests">{C.all}</Link>
      </p>
    </div>
  );
}
