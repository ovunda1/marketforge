// How it works (M11): the architecture in a few paragraphs, with links to the code and docs.
import { Link } from "react-router-dom";

import { REPO_URL } from "../App";
import { Architecture } from "../components/Architecture";
import { Panel } from "../components/Panel";

const blob = (p: string) => `${REPO_URL}/blob/main/${p}`;
const LINKS: [string, string, string][] = [
  ["engine/src/order_book.cpp", "The order book", "Price-time priority matching, C++20, no allocation on the hot path"],
  ["docs/risk.md", "Pre-trade risk", "Every check, its reject code, and the order they run in"],
  ["docs/journal-format.md", "The journal", "Binary, append-only; replay reproduces every event exactly"],
  ["services/matcher/core.py", "The matcher", "The single writer: group commit per batch, idempotency, restart"],
  ["services/api/app.py", "The gateway", "FastAPI REST and WebSocket; never loads the engine"],
  ["services/client.py", "Market-data client", "Snapshot + numbered deltas; this dashboard runs a port of it"],
  ["research/runs.py", "Backtests", "Event-driven and bar backtests, walk-forward, run records"],
  ["research/backtest/costs.py", "Costs", "Commission, SEC fee, spread, impact, financing"],
  ["bench/RESULTS.md", "Benchmarks", "How the throughput and latency numbers were measured"],
  ["docs/services.md", "Services reference", "Processes, configuration, REST and WebSocket"],
];

export function HowItWorks() {
  return (
    <div className="page">
      <div className="page-head">
        <h1>How it works</h1>
        <p>MarketForge is a small exchange built the way real ones are: one process decides the order of everything,
          writes it down before telling anyone, and everything else is a copy that can be rebuilt from that record.</p>
      </div>
      <Panel id="arch" title="Architecture">
        <div className="panel-b"><Architecture /></div>
      </Panel>
      <Panel id="path" title="The life of an order">
        <div className="panel-b prose">
          <ol>
            <li><b>You click Place order.</b> The browser sends it to an API gateway over REST. The price is already an
              integer number of ticks: the dashboard converts your text exactly, never through floating point.</li>
            <li><b>The gateway forwards it</b> over a Redis stream to the matcher. Gateways hold no state; there can be
              any number of them.</li>
            <li><b>The matcher batches commands.</b> For each batch it first writes and fsyncs an intake log, then the
              journal record, runs the engine (risk checks, then matching), and fsyncs the journal. Only then do replies
              and market data leave.</li>
            <li><b>Market data fans out</b> through Redis to every gateway, which streams it to browsers as a snapshot
              followed by numbered updates. A missed number or a matcher restart makes the page fetch a fresh snapshot.</li>
            <li><b>Postgres is a projection.</b> A separate process replays the journals into tables for order
              history and positions; it can be deleted and rebuilt at any time.</li>
          </ol>
          <h2>Why it is built this way</h2>
          <ul>
            <li><b>One writer per symbol</b>: no locks in the book, and a total order that replay reproduces exactly.</li>
            <li><b>Integer prices</b> everywhere, in C++, Python and this page: no rounding, ever.</li>
            <li><b>The journal is the source of truth</b>: a crash loses nothing that was acknowledged.</li>
            <li><b>Honest research</b>: costs are on by default, results are out-of-sample, and every run is recorded
              so overfitting can't hide. See <Link to="/backtests">Backtests</Link>.</li>
          </ul>
          <p>The REST API is documented at <a href="/api/docs">/api/docs</a> (use the Authorize button with a practice
            account's key).</p>
        </div>
      </Panel>
      <Panel id="code" title="Read the code">
        <div className="panel-b links">
          {LINKS.map(([path, title, text]) => (
            <a key={path} href={blob(path)}>{title}<small>{text}</small><small className="num">{path}</small></a>
          ))}
        </div>
      </Panel>
    </div>
  );
}
