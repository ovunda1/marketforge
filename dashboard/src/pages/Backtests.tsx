// Backtests (M11): every run on this server (failed ones included) and the committed
// showcase runs. Real tickers appear only on backtest pages, always marked as historical data.
import { Link } from "react-router-dom";

import { ErrorNote } from "../components/ErrorNote";
import { Info } from "../components/Info";
import { Panel } from "../components/Panel";
import { api } from "../lib/api";
import { pct } from "../lib/format";
import { useAsync } from "../lib/hooks";
import { explain } from "../lib/rejects";
import { DSR_THRESHOLD } from "../lib/runSummary";
import { useTech } from "../lib/tech";

interface Headline { sharpe: { gross: number; net: number }; dsr?: { net: number }; annual_return?: { net: number };
                     period?: { start: string; end: string }; trials?: { backtests: number; dsr_trials: number } }
export interface RunRow {
  id: string; status: string; featured: boolean; strategy: string | null; dataset: string; real_data: boolean;
  costs: boolean; start: string | null; end: string | null; symbols: string[] | null; created_at?: string | null;
  error?: string | null; headline: Headline | null;
}
export interface Counts { total: number; queued: number; running: number; done: number; failed: number }
export interface RunList { runs: RunRow[]; featured: RunRow[]; counts: Counts }

export const STRATEGY_LABEL: Record<string, string> = { tsmom: "Time-series momentum", xsmom: "Cross-sectional momentum",
                                                        obmom: "Order-book momentum" };

export function DataChip({ real }: { real: boolean }) {
  return real ? <span className="chip real">historical data</span> : <span className="chip">synthetic data</span>;
}

export function OverfittingNote({ counts, trials }: { counts: Counts | null; trials?: number }) {
  return (
    <p className="note notice" data-testid="overfitting-note">
      <strong>Every run is recorded <Info term="overfitting" label="overfitting" /></strong>
      {counts ? <>This server has run <b data-testid="total-runs">{counts.total}</b> backtest{counts.total === 1 ? "" : "s"}
        {counts.failed ? ` (${counts.failed} failed)` : ""}. </> : null}
      Trying many variations and keeping the best makes any strategy look better than it is, so every run stays in
      this list, and each result is judged with a deflated Sharpe ratio that allows for the variations it tried
      {trials ? ` (${trials} in this run's own parameter grid)` : ""}.
    </p>
  );
}

function verdict(h: Headline | null): string {
  if (!h?.dsr) return "–";
  return h.dsr.net >= DSR_THRESHOLD ? "significant" : "not significant";
}

function Rows({ rows, featured }: { rows: RunRow[]; featured?: boolean }) {
  const { show } = useTech();
  return (
    <div className="scroll-x">
      <table className="data runs">
        <thead>
          <tr>
            <th scope="col">Run</th><th scope="col">Status</th><th scope="col">Data</th><th scope="col">Period</th>
            <th scope="col" className="num">Sharpe (net) <Info term="sharpe" label="the Sharpe ratio" /></th>
            <th scope="col">Significance <Info term="dsr" label="significance" /></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} data-testid="run-row">
              <th scope="row">
                <Link to={`/backtests/${r.id}`}>{STRATEGY_LABEL[r.strategy ?? ""] ?? r.strategy ?? "Backtest"}</Link>
                {show && !featured && <span className="src"> {r.id}</span>}
                {featured && <span className="src"> showcase</span>}
              </th>
              <td><span className={`status-pill ${r.status}`}>{r.status}</span></td>
              <td>{r.dataset} <DataChip real={r.real_data} /></td>
              <td className="num">{r.headline?.period ? `${r.headline.period.start.slice(0, 4)}–${r.headline.period.end.slice(0, 4)}`
                : r.start ? `${r.start.slice(0, 4)}–${(r.end ?? "").slice(0, 4)}` : "–"}</td>
              <td className="num">{r.headline ? r.headline.sharpe.net.toFixed(2) : "–"}</td>
              <td>{verdict(r.headline)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Backtests() {
  const list = useAsync(() => api<RunList>("/backtests?limit=100"), []);
  const d = list.data;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Backtests</h1>
        <p>A backtest replays historical prices through a trading strategy to see how it would have done, after the
          costs real trading pays. Results are out-of-sample: each year is tested with settings chosen only from the
          years before it.</p>
      </div>
      <div className="cta">
        <Link className="btn primary" to="/backtests/new" data-testid="new-backtest">Run a backtest →</Link>
      </div>
      {list.error ? <ErrorNote e={explain(list.error)} /> : null}
      <OverfittingNote counts={d?.counts ?? null} />
      <Panel id="featured" title="Showcase runs" right={<span className="src">research/showcase · fixed before the first real run</span>}>
        {d ? <Rows rows={d.featured} featured /> : <p className="empty">Loading…</p>}
      </Panel>
      <Panel id="runs" title="Runs on this server" right={<span className="src">newest first</span>}>
        {d && d.runs.length === 0 ? <p className="empty">No runs yet. Start one with “Run a backtest”.</p>
          : d ? <Rows rows={d.runs} /> : <p className="empty">Loading…</p>}
      </Panel>
    </div>
  );
}
