// Behind "Show technical details" (M11): the feed's internals and the last requests.
import { useEffect, useReducer } from "react";

import { onRequest, recentRequests, wsUrl } from "../lib/api";
import type { ExecutionsFeed, MarketFeed } from "../lib/feeds";
import { useTick } from "../lib/hooks";
import { Panel } from "./Panel";

export function TechDetails({ feed, executions }: { feed: MarketFeed | null; executions: ExecutionsFeed | null }) {
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  useEffect(() => onRequest(rerender), []);
  useTick(1000);
  const s = feed?.stats;
  const age = s?.lastMessageAt ? `${((Date.now() - s.lastMessageAt) / 1000).toFixed(1)} s ago` : "–";
  return (
    <Panel id="tech" title="Technical details" className="tech">
      <div className="panel-b" data-testid="tech-details">
        <dl>
          <div><dt>WebSocket</dt><dd>{feed ? wsUrl(`/ws/marketdata/${feed.symbol}`) : "–"}</dd></div>
          <div><dt>Messages</dt><dd>{s?.messages ?? 0}</dd></div>
          <div><dt>Last message</dt><dd>{age}</dd></div>
          <div><dt>Gaps detected</dt><dd data-testid="gaps">{s?.gaps ?? 0}</dd></div>
          <div><dt>Resnapshots</dt><dd data-testid="resnapshots">{s?.resnapshots ?? 0}</dd></div>
          <div><dt>Reconnects</dt><dd>{s?.reconnects ?? 0}</dd></div>
          <div><dt>Last close code</dt><dd>{s?.closeCode ?? "–"}</dd></div>
          <div><dt>Executions feed</dt><dd>{executions ? (executions.connected ? "subscribed" : "connecting") : "no account"}</dd></div>
        </dl>
        <div className="scroll-x">
          <table className="data">
            <caption className="sr-only">Recent requests to the gateway</caption>
            <thead><tr><th scope="col">Request</th><th scope="col" className="num">Status</th><th scope="col" className="num">ms</th>
              <th scope="col">Worker</th><th scope="col">Request id</th></tr></thead>
            <tbody>
              {recentRequests().map((r, i) => (
                <tr key={i}><td className="num">{r.method} {r.path}</td><td className="num">{r.status}</td>
                  <td className="num">{r.ms.toFixed(1)}</td><td className="num">{r.worker ?? "–"}</td>
                  <td className="num">{r.requestId?.slice(0, 12) ?? "–"}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Panel>
  );
}
