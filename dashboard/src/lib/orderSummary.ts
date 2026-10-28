// The sentence under the order form (M11): what the order will do, in plain words, before
// it is placed. It follows the engine's rules (docs/order-types.md): a market order is
// immediate-or-cancel and never rests; post-only is rejected if it would cross; FOK
// fills completely or is rejected; the remainder of a resting order waits in the book.
// The words are in copy.ts.

import { orderSummary as S } from "../copy";
import { formatTicks } from "./money";

export type Side = "buy" | "sell";
export type OrderType = "limit" | "market";
export type Tif = "gtc" | "day" | "ioc" | "fok";

export interface Draft {
  symbol: string;
  tickSize: string;
  side: Side;
  type: OrderType;
  tif: Tif;
  price: number | null; // ticks; null while the field is empty or invalid
  qty: number | null;
  postOnly: boolean;
}

export interface Top {
  bestBid: number | null;
  bestAsk: number | null;
}

const shares = (q: number) => S.shares(q.toLocaleString("en-US"), q === 1);

export function summarize(d: Draft, top: Top): string {
  const buy = d.side === "buy";
  const verb = S.verb[d.side];
  const money = (t: number) => `$${formatTicks(t, d.tickSize)}`;
  const other = buy ? top.bestAsk : top.bestBid;
  if (d.qty === null) return S.needQty;

  if (d.type === "market") {
    const head = S.marketHead(verb, shares(d.qty), d.symbol);
    return other === null ? S.marketNobody(head, buy) : S.marketFrom(head, money(other));
  }

  if (d.price === null) return S.needPrice;
  const head = S.limitHead(verb, shares(d.qty), d.symbol, buy, money(d.price));
  const crosses = other !== null && (buy ? d.price >= other : d.price <= other);
  const rests = d.tif === "gtc" || d.tif === "day";
  const until = d.tif === "day" ? S.until.day : S.until.gtc;

  if (d.postOnly) return crosses ? S.postCross(head, buy, money(other!)) : S.postRest(head, until);
  if (crosses) {
    const now = S.crossNow(buy, money(other!));
    if (d.tif === "fok") return S.crossFok(head, now, shares(d.qty));
    if (d.tif === "ioc") return S.crossIoc(head, now);
    return S.crossRest(head, now, until);
  }
  const nobody = other === null ? S.nobody(buy) : S.best(buy, money(other));
  return rests ? S.rest(head, nobody, until, buy) : S.noRest(head, nobody, d.tif === "fok");
}
