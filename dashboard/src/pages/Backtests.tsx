// Backtests (M11, M11b): every run on this server (failed ones included) and the committed
// showcase runs. Real tickers appear only on backtest pages, always marked as historical data.
import { Link } from "react-router-dom";

import { ErrorNote } from "../components/ErrorNote";
import { Panel } from "../components/Panel";
import { Term } from "../components/Term";
import { backtests as C, site, strategies } from "../copy";
import { api } from "../lib/api";
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

export const STRATEGY_LABEL = strategies;

export function DataChip({ real }: { real: boolean }) {
  return real ? <span className="chip real">{C.historical}</span> : <span className="chip">{C.synthetic}</span>;
}

export function OverfittingNote({ counts, trials }: { counts: Counts | null; trials?: number }) {
  return (
    <div className="note" data-testid="overfitting-note">
      <strong><Term k="overfitting">{C.recorded}</Term></strong>
      <p>
        {counts ? <>{C.serverRuns} <b data-testid="total-runs">{counts.total}</b>
          {C.serverRunsTail(counts.total, counts.failed)}</> : null}
        {C.overfitting(trials ?? null)}
      </p>
    </div>
  );
}

function verdict(h: Headline | null): string {
  if (!h?.dsr) return "–";
  return h.dsr.net >= DSR_THRESHOLD ? C.significant : C.notSignificant;
}

function Rows({ rows, featured }: { rows: RunRow[]; featured?: boolean }) {
  const { show } = useTech();
  return (
    <div className="scroll-x">
      <table className="data runs">
        <thead>
          <tr>
            <th scope="col">{C.cols.run}</th><th scope="col">{C.cols.status}</th><th scope="col">{C.cols.data}</th>
            <th scope="col" className="num">{C.cols.period}</th>
            <th scope="col" className="num"><Term k="sharpe">{C.cols.sharpe}</Term></th>
            <th scope="col"><Term k="dsr">{C.cols.significance}</Term></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} data-testid="run-row">
              <th scope="row">
                <Link to={`/backtests/${r.id}`}>{STRATEGY_LABEL[r.strategy ?? ""] ?? r.strategy ?? C.backtest}</Link>
                {show && !featured && <span className="src mono"> {r.id}</span>}
                {featured && <span className="src"> {C.showcase}</span>}
              </th>
              <td><span className={`status-pill ${r.status}`}>{r.status}</span></td>
              <td>{r.dataset} <DataChip real={r.real_data} /></td>
              <td className="num">{r.headline?.period ? `${r.headline.period.start.slice(0, 4)}–${r.headline.period.end.slice(0, 4)}`
                : r.start ? `${r.start.slice(0, 4)}–${(r.end ?? "").slice(0, 4)}` : "–"}</td>
              <td className="num hi">{r.headline ? r.headline.sharpe.net.toFixed(2) : "–"}</td>
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
      <header className="page-head">
        <h1>{C.title}</h1>
        <p>{C.intro}</p>
        <p className="advice" data-testid="not-advice">{site.notAdvice}</p>
        <div><Link className="btn primary" to="/backtests/new" data-testid="new-backtest">{C.run}</Link></div>
      </header>
      {list.error ? <ErrorNote e={explain(list.error)} /> : null}
      <OverfittingNote counts={d?.counts ?? null} />
      <Panel id="featured" n="01" title={C.featured} right={<span className="src">{C.featuredNote}</span>}>
        {d ? <Rows rows={d.featured} featured /> : <p className="empty">{C.loading}</p>}
      </Panel>
      <Panel id="runs" n="02" title={C.serverList} right={<span className="src">{C.newestFirst}</span>}>
        {d && d.runs.length === 0 ? <p className="empty">{C.empty}</p>
          : d ? <Rows rows={d.runs} /> : <p className="empty">{C.loading}</p>}
      </Panel>
    </div>
  );
}
