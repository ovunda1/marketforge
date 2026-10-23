// The trade tape (M11): newest first; the aggressor side as an arrow and a word.
import type { MarketFeed } from "../lib/feeds";
import { formatInt, formatTicks } from "../lib/money";
import { useTech } from "../lib/tech";
import { Info } from "./Info";
import { Panel } from "./Panel";

export function Tape({ feed, tickSize }: { feed: MarketFeed | null; tickSize: string }) {
  const { show } = useTech();
  const trades = (feed?.trades ?? []).slice(0, 12);
  return (
    <Panel id="tape" title={<>Trades <Info term="trades" label="trades" /></>}>
      {trades.length === 0 ? <p className="empty">No trades yet.</p> : (
        <table>
          <caption className="sr-only">Recent trades, newest first</caption>
          <thead>
            <tr>
              {show && <th scope="col" className="num">seq_no</th>}
              <th scope="col" className="num">Price</th>
              <th scope="col" className="num">Qty</th>
              <th scope="col">Aggressor <Info term="aggressor" label="the aggressor" /></th>
            </tr>
          </thead>
          <tbody data-testid="tape">
            {trades.map((t) => (
              <tr key={t.trade_id}>
                {show && <td className="num muted">{formatInt(t.seq_no)}</td>}
                <td className="num">{formatTicks(t.price, tickSize)}</td>
                <td className="num">{formatInt(t.qty)}</td>
                <td><span className={`tag ${t.aggressor_side}`}>{t.aggressor_side === "buy" ? "▲ buy" : "▼ sell"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
