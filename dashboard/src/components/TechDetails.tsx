// Behind "Show technical details" (M11): the feed's internals and the last requests.
import { useEffect, useReducer } from "react";

import { tech as C } from "../copy";
import { onRequest, recentRequests, wsUrl } from "../lib/api";
import type { ExecutionsFeed, MarketFeed } from "../lib/feeds";
import { useTick } from "../lib/hooks";
import { Panel } from "./Panel";

export function TechDetails({ feed, executions }: { feed: MarketFeed | null; executions: ExecutionsFeed | null }) {
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  useEffect(() => onRequest(rerender), []);
  useTick(1000);
  const s = feed?.stats;
  const age = s?.lastMessageAt ? C.ago(((Date.now() - s.lastMessageAt) / 1000).toFixed(1)) : "–";
  return (
    <Panel id="tech" title={C.title} className="tech">
      <div className="panel-b" data-testid="tech-details">
        <dl>
          <div><dt>{C.ws}</dt><dd>{feed ? wsUrl(`/ws/marketdata/${feed.symbol}`) : "–"}</dd></div>
          <div><dt>{C.messages}</dt><dd>{s?.messages ?? 0}</dd></div>
          <div><dt>{C.lastMessage}</dt><dd>{age}</dd></div>
          <div><dt>{C.gaps}</dt><dd data-testid="gaps">{s?.gaps ?? 0}</dd></div>
          <div><dt>{C.resnapshots}</dt><dd data-testid="resnapshots">{s?.resnapshots ?? 0}</dd></div>
          <div><dt>{C.reconnects}</dt><dd>{s?.reconnects ?? 0}</dd></div>
          <div><dt>{C.closeCode}</dt><dd>{s?.closeCode ?? "–"}</dd></div>
          <div><dt>{C.executions}</dt><dd>{executions ? (executions.connected ? C.subscribed : C.connecting) : C.noAccount}</dd></div>
        </dl>
        <div className="scroll-x">
          <table className="data">
            <caption className="sr-only">{C.caption}</caption>
            <thead><tr><th scope="col">{C.cols.request}</th><th scope="col" className="num">{C.cols.status}</th>
              <th scope="col" className="num">{C.cols.ms}</th><th scope="col">{C.cols.worker}</th><th scope="col">{C.cols.id}</th></tr></thead>
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
