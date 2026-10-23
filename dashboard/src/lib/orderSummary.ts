// The sentence under the order form (M11): what the order will do, in plain words, before
// it is placed. It follows the engine's rules (docs/order-types.md): a market order is
// immediate-or-cancel and never rests; post-only is rejected if it would cross; FOK
// fills completely or is rejected; the remainder of a resting order waits in the book.

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

const shares = (q: number) => `${q.toLocaleString("en-US")} ${q === 1 ? "share" : "shares"}`;

export function summarize(d: Draft, top: Top): string {
  const buy = d.side === "buy";
  const verb = buy ? "Buy" : "Sell";
  const money = (t: number) => `$${formatTicks(t, d.tickSize)}`;
  const other = buy ? top.bestAsk : top.bestBid;
  if (d.qty === null) return "Enter a quantity to see what this order will do.";

  if (d.type === "market") {
    const head = `${verb} ${shares(d.qty)} of ${d.symbol} at the best prices available now.`;
    if (other === null) {
      return `${head} There are no ${buy ? "sellers" : "buyers"} right now, so the exchange would reject it.`;
    }
    return `${head} Starting at ${money(other)}; whatever can't fill immediately is cancelled, nothing waits in the book.`;
  }

  if (d.price === null) return "Enter a price to see what this order will do.";
  const limit = buy ? `at ${money(d.price)} or less` : `at ${money(d.price)} or more`;
  const head = `${verb} ${shares(d.qty)} of ${d.symbol} ${limit}.`;
  const crosses = other !== null && (buy ? d.price >= other : d.price <= other);
  const rests = d.tif === "gtc" || d.tif === "day";
  const until = d.tif === "day" ? "until you cancel it or the session closes" : "until you cancel it";

  if (d.postOnly) {
    if (crosses) {
      return `${head} Post-only: at this price it would trade with the ${buy ? "seller at" : "buyer at"} ` +
        `${money(other!)} straight away, so the exchange would reject it. Move the price ` +
        `${buy ? "below" : "above"} ${money(other!)}.`;
    }
    return `${head} Post-only: it only adds to the book, never trades on arrival, and waits ${until}.`;
  }
  if (crosses) {
    const now = `${buy ? "Sellers are offering" : "Buyers are bidding"} ${money(other!)}, so it should start filling right away`;
    if (d.tif === "fok") return `${head} ${now}, but only if all ${shares(d.qty)} can fill at once; otherwise nothing trades.`;
    if (d.tif === "ioc") return `${head} ${now}; anything not filled immediately is cancelled.`;
    return `${head} ${now}; anything left waits in the book ${until}.`;
  }
  const nobody = other === null ? `There are no ${buy ? "sellers" : "buyers"} right now`
    : `The best ${buy ? "offer is" : "bid is"} ${money(other)}`;
  if (rests) {
    return `${head} ${nobody}, so it won't fill yet: it waits in the book ${until}, or until ` +
      `${buy ? "a seller comes down" : "a buyer comes up"} to your price.`;
  }
  return `${head} ${nobody}, so it would ${d.tif === "fok" ? "be rejected" : "be cancelled"} without trading.`;
}
