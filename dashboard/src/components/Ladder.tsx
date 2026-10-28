// The price ladder (M11): L2 depth from the market feed. Asks above, bids below, each side
// labelled with a word and an arrow (never color alone). Clicking a price (or pressing
// Enter on it) puts it in the order form. Levels with your own open orders are marked.
import { useMemo } from "react";

import type { Level } from "../lib/bookClient";
import type { MarketFeed } from "../lib/feeds";
import { formatInt, formatTicks } from "../lib/money";
import { Info } from "./Info";
import { Panel } from "./Panel";

const LEVELS = 8;

export function Ladder({ feed, tickSize, onPick, mine }: {
  feed: MarketFeed | null; tickSize: string; onPick: (price: number) => void; mine: Set<number>;
}) {
  const bids = feed?.client.synced ? feed.client.bids().slice(0, LEVELS) : [];
  const asks = feed?.client.synced ? feed.client.asks().slice(0, LEVELS) : [];
  const maxQty = useMemo(() => Math.max(1, ...bids.map((b) => b[1]), ...asks.map((a) => a[1])), [bids, asks]);
  const px = (t: number) => formatTicks(t, tickSize);

  const row = (side: "bid" | "ask", [p, q, n]: Level) => {
    const bar = <span className="bar" style={{ width: `${(q / maxQty) * 100}%` }} aria-hidden="true" />;
    const qty = <span className="q">{formatInt(q)}</span>;
    return (
      <tr key={`${side}${p}`} className={side} data-testid={`${side}-row`}>
        <td className="num qty">{side === "bid" && <>{bar}{qty}</>}</td>
        <th scope="row" className="num price">
          <button type="button" onClick={() => onPick(p)}
                  aria-label={`${side === "bid" ? "Bid" : "Ask"} ${px(p)}, ${formatInt(q)} shares in ${n} order${n === 1 ? "" : "s"}. Use this price.`}>
            {px(p)}{mine.has(p) && <span className="mine" aria-hidden="true">●</span>}
          </button>
        </th>
        <td className="num qty">{side === "ask" && <>{bar}{qty}</>}</td>
        <td className="num orders">{n}</td>
      </tr>
    );
  };
  const spread = bids[0] && asks[0] ? asks[0][0] - bids[0][0] : null;
  return (
    <Panel id="book" focal className="ladder" title={<>Order book <Info term="orderBook" label="the order book" /></>}
           right={<span className="src">top {LEVELS} levels · click a price to use it</span>}>
      <table className="ladder-table">
        <caption className="sr-only">Order book: asks (sell orders) above the spread, bids (buy orders) below</caption>
        <thead>
          <tr>
            <th scope="col" className="num"><span className="tag buy">▲ Bids</span> <Info term="bids" label="bids" /></th>
            <th scope="col" className="c">Price</th>
            <th scope="col"><span className="tag sell">▼ Asks</span> <Info term="asks" label="asks" /></th>
            <th scope="col" className="num">Orders <Info term="orders" label="orders" /></th>
          </tr>
        </thead>
        <tbody data-testid="ladder">
          {!feed?.client.synced && <tr className="empty-side"><td colSpan={4}>Loading the book…</td></tr>}
          {feed?.client.synced && asks.length === 0 && <tr className="empty-side"><td colSpan={4}>No sellers</td></tr>}
          {[...asks].reverse().map((a) => row("ask", a))}
          <tr className="spread-row">
            <td colSpan={4}>
              spread <span className="num" data-testid="spread">{spread !== null ? px(spread) : "–"}</span>{" "}
              <Info term="spread" label="the spread" />
            </td>
          </tr>
          {bids.map((b) => row("bid", b))}
          {feed?.client.synced && bids.length === 0 && <tr className="empty-side"><td colSpan={4}>No buyers</td></tr>}
        </tbody>
      </table>
    </Panel>
  );
}
