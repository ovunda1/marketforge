// The order book (M11, M11b): L2 depth from the market feed. Asks above, bids below, each side
// named in words (asks red, bids green, never color alone). Selecting a price (click, or Enter on
// it) puts it in the order form. Levels with your own open orders are marked.
import { useMemo } from "react";

import { book as C, market } from "../copy";
import type { Level } from "../lib/bookClient";
import type { MarketFeed } from "../lib/feeds";
import { formatInt, formatTicks } from "../lib/money";
import { Panel } from "./Panel";
import { Term } from "./Term";

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
          <button type="button" onClick={() => onPick(p)} aria-label={C.pick(side, px(p), formatInt(q), n)}>
            {px(p)}{mine.has(p) && <span className="mine" aria-hidden="true">●</span>}
          </button>
        </th>
        <td className="num qty">{side === "ask" && <>{bar}{qty}</>}</td>
        <td className="num orders">{n}</td>
      </tr>
    );
  };
  const spread = bids[0] && asks[0] ? asks[0][0] - bids[0][0] : null;
  // Space for the missing levels (no empty rows), so the spread stays in the middle of the panel
  // and the book keeps its height as levels come and go.
  const ROW = 46; // px, .ladder td height
  const space = (have: number) => have >= LEVELS ? null : (
    <tr className="spacer" aria-hidden="true"><td colSpan={4} style={{ height: (LEVELS - Math.max(have, 1)) * ROW }} /></tr>);
  return (
    <Panel id="book" n="01" className="ladder" title={market.sections.book} right={<span className="src">{C.hint(LEVELS)}</span>}>
      <table className="ladder-table">
        <caption className="sr-only">{C.caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="num"><span className="buy-ink">{C.bids}</span><span className="sub">{C.bidsSub}</span></th>
            <th scope="col" className="c">{C.price}</th>
            <th scope="col"><span className="sell-ink">{C.asks}</span><span className="sub">{C.asksSub}</span></th>
            <th scope="col" className="num"><Term k="orders">{C.orders}</Term></th>
          </tr>
        </thead>
        <tbody data-testid="ladder">
          {space(asks.length)}
          {!feed?.client.synced && <tr className="empty-side"><td colSpan={4}>{C.loading}</td></tr>}
          {feed?.client.synced && asks.length === 0 && <tr className="empty-side"><td colSpan={4}>{C.noSellers}</td></tr>}
          {[...asks].reverse().map((a) => row("ask", a))}
          <tr className="spread-row">
            <td colSpan={4}>
              <Term k="spread">{C.spread}</Term> <span className="num hi" data-testid="spread">{spread !== null ? px(spread) : "–"}</span>
            </td>
          </tr>
          {bids.map((b) => row("bid", b))}
          {feed?.client.synced && bids.length === 0 && <tr className="empty-side"><td colSpan={4}>{C.noBuyers}</td></tr>}
          {space(bids.length)}
        </tbody>
      </table>
    </Panel>
  );
}
