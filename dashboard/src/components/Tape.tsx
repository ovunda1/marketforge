// The trade tape (M11, M11b): newest first; the side that started each trade as a word.
import { market, tape as C } from "../copy";
import type { MarketFeed } from "../lib/feeds";
import { formatInt, formatTicks } from "../lib/money";
import { useTech } from "../lib/tech";
import { Panel } from "./Panel";
import { Term } from "./Term";

export function Tape({ feed, tickSize, n }: { feed: MarketFeed | null; tickSize: string; n: string }) {
  const { show } = useTech();
  const trades = (feed?.trades ?? []).slice(0, 10);
  return (
    <Panel id="tape" n={n} title={market.sections.tape}>
      {trades.length === 0 ? <p className="empty">{C.empty}</p> : (
        <table className="data">
          <caption className="sr-only">{C.caption}</caption>
          <thead>
            <tr>
              {show && <th scope="col" className="num">{C.seqNo}</th>}
              <th scope="col" className="num">{C.price}</th>
              <th scope="col" className="num">{C.qty}</th>
              <th scope="col" className="num"><Term k="aggressor">{C.side}</Term></th>
            </tr>
          </thead>
          <tbody data-testid="tape">
            {trades.map((t) => (
              <tr key={t.trade_id}>
                {show && <td className="num muted mono">{formatInt(t.seq_no)}</td>}
                <td className="num">{formatTicks(t.price, tickSize)}</td>
                <td className="num">{formatInt(t.qty)}</td>
                <td className="num"><span className={`side-word ${t.aggressor_side}`}>
                  {t.aggressor_side === "buy" ? C.buy : C.sell}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
