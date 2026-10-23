// One backtest's results (M11): a plain-language first sentence (including when the result
// is not statistically significant), live progress while it runs, then the equity curve,
// drawdown, cost breakdown and the gross-vs-net table from its metrics.json.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { CostBars, LineChart } from "../components/Charts";
import { ErrorNote } from "../components/ErrorNote";
import { Info } from "../components/Info";
import { Panel } from "../components/Panel";
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
const STAGE_TEXT: Record<string, string> = { queued: "Waiting for a worker", running: "Started", validated: "Settings checked",
  data_loaded: "Data loaded", simulated: "Strategy simulated", written: "Results written", done: "Done" };

const ROWS: [string, string, (v: number | null) => string][] = [
  ["annual_return", "Annual return", (v) => pct(v)], ["volatility", "Volatility (annual)", (v) => pct(v)],
  ["sharpe", "Sharpe ratio", (v) => num(v)], ["sortino", "Sortino ratio", (v) => num(v)],
  ["max_drawdown", "Max drawdown", (v) => pct(v, 1)], ["calmar", "Calmar ratio", (v) => num(v)],
  ["daily_hit_rate", "Days up", (v) => pct(v, 1)], ["total_return", "Total return", (v) => pct(v)],
  ["dsr", "Deflated Sharpe ratio", (v) => num(v)],
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

  if (view.error) return <div className="page"><ErrorNote e={explain(view.error)} /><Link to="/backtests">All backtests</Link></div>;
  const v = view.data;
  if (!v) return <div className="page"><p className="muted">Loading…</p></div>;
  const m = v.metrics;
  const s = v.summary;
  const run = m ? summarizeRun(m) : null;
  const reached = Math.max(0, ...stages.map((x) => STAGES.indexOf(x)), STAGES.indexOf(v.status));

  return (
    <div className="page">
      <div className="page-head run-head">
        <h1>{STRATEGY_LABEL[s.strategy ?? ""] ?? "Backtest"}{v.featured ? " (showcase)" : ""}</h1>
        {run && <p className={`lead ${run.significant === false ? "not-significant" : run.significant ? "significant" : ""}`}
                   data-testid="run-summary">{run.sentence}</p>}
        <div className="meta">
          <span>{s.dataset} <DataChip real={s.real_data} /></span>
          {m?.period && <span>out of sample <b className="num">{m.period.start} – {m.period.end}</b></span>}
          {s.symbols && <span>{s.real_data ? "tickers (historical data)" : "symbols"}: <b className="num">{s.symbols.join(" ")}</b></span>}
          <span>costs <b>{s.costs ? "on" : "off"}</b></span>
          {v.config?.walkforward && <span>train <b className="num">{v.config.walkforward.train}</b> days, test <b className="num">{v.config.walkforward.test}</b></span>}
          {m?.trials && <span>backtests in this run <b className="num">{m.trials.backtests}</b></span>}
          {v.commit && <span>commit <b className="num">{v.commit.slice(0, 7)}</b></span>}
        </div>
      </div>

      {(v.status === "queued" || v.status === "running") && (
        <Panel id="progress" title="Progress">
          <div className="panel-b progress" role="status" aria-live="polite" data-testid="progress">
            <div className="bar-track" aria-hidden="true"><div className="bar-fill" style={{ width: `${(reached / (STAGES.length - 1)) * 100}%` }} /></div>
            <ol>{STAGES.slice(0, reached + 1).map((x) => <li key={x}>{STAGE_TEXT[x] ?? x}</li>)}</ol>
          </div>
        </Panel>
      )}
      {v.status === "failed" && (
        <div className="note error" role="alert" data-testid="run-failed">
          <strong>This run failed</strong><p>{v.error}</p>
          <p>Failed runs stay in the list: they count as runs made.</p>
        </div>
      )}

      <OverfittingNote counts={counts.data ?? null} trials={m?.trials?.dsr_trials} />

      {m?.series && (
        <>
          <Panel id="equity" title={<>Cumulative return, before and after costs <Info term="grossNet" label="gross and net" /></>}
                 right={<span className="src">metrics.json · series</span>}>
            <div className="panel-b">
              <LineChart title="Cumulative return over the starting capital, gross and net of costs" dates={m.series.dates}
                         series={[{ key: "gross", label: "Before costs (gross)", values: m.series.gross_cum_return },
                                  { key: "net", label: "After costs (net)", values: m.series.net_cum_return }]} />
            </div>
          </Panel>
          <Panel id="drawdown" title={<>Drawdown, after costs <Info term="drawdown" label="drawdown" /></>}>
            <div className="panel-b">
              <LineChart title="Drawdown from the previous peak, net of costs" dates={m.series.dates} area
                         series={[{ key: "net", label: "Drawdown (net)", values: m.series.net_drawdown.map((x) => -Math.abs(x)) }]} />
            </div>
          </Panel>
        </>
      )}

      {m && (
        <div className="cols" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <Panel id="gross-net" title="Gross vs net" right={<span className="src">metrics.json · performance</span>}>
            <table className="data" data-testid="gross-net">
              <caption className="sr-only">Performance before (gross) and after (net) costs</caption>
              <thead><tr><th scope="col">Metric</th><th scope="col" className="num">Gross</th><th scope="col" className="num">Net of costs</th></tr></thead>
              <tbody>
                {ROWS.filter(([k]) => m.performance[k]).map(([k, label, f]) => (
                  <tr key={k}><th scope="row">{label}{k === "dsr" && <> <Info term="dsr" label="the deflated Sharpe ratio" /></>}</th>
                    <td className="num">{f(m.performance[k]!.gross)}</td><td className="num hi">{f(m.performance[k]!.net)}</td></tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel id="costs" title={<>Cost breakdown <Info term="costs" label="costs" /></>} right={<span className="src">metrics.json · costs</span>}>
            <div className="panel-b">
              {m.costs ? <CostBars byComponent={m.costs.by_component} /> : <p className="muted">No cost data.</p>}
              {m.cost_drag && (
                <p className="foot">Costs took {pct(m.cost_drag.annual_cost)} a year off the return
                  {m.costs?.bps_of_traded_notional != null ? `, ${num(m.costs.bps_of_traded_notional, 1)} basis points of every dollar traded` : ""}.
                  {m.costs && ` Total: ${microDollars(Object.values(m.costs.by_component).reduce<bigint>((a, x) => a + BigInt(x), 0n).toString())}.`}</p>
              )}
            </div>
          </Panel>
        </div>
      )}

      <p className="hint">
        {v.tearsheet_url && <><a href={`${API_BASE}${v.tearsheet_url}`}>Open the full tearsheet</a> · </>}
        <Link to="/backtests">All backtests</Link>
      </p>
    </div>
  );
}
