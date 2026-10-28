// What happened to a placed order, as a sentence (M11), from the gateway's OrderOut. Words: copy.ts.
import { orderResult as R } from "../copy";
import { formatInt, formatTicks } from "./money";
import { explainCancel, explainReject } from "./rejects";

export interface Fill { trade_id: number; price: number; qty: number; liquidity: string; seq_no: number }
export interface OrderOut {
  order_id: number;
  status: string;
  symbol?: string | null;
  side?: string | null;
  type?: string | null;
  tif?: string | null;
  price?: number | null;
  qty?: number | null;
  leaves_qty?: number | null;
  cum_qty?: number | null;
  reject_reason?: string | null;
  cancel_reason?: string | null;
  seq_no?: number | null;
  fills: Fill[];
}

export function describeResult(o: OrderOut, tickSize: string): { tone: "ok" | "info" | "error"; title: string; text: string } {
  const px = (t: number) => `$${formatTicks(t, tickSize)}`;
  const did = o.side === "sell" ? R.did.sell : R.did.buy;
  const fills = o.fills ?? [];
  const filled = fills.reduce((n, f) => n + f.qty, 0);
  const at = fills.map((f) => R.fill(formatInt(f.qty), px(f.price))).join(", ");
  const sym = o.symbol ?? "";
  const ref = R.ref(o.order_id);
  if (o.status === "rejected") {
    const e = explainReject(o.reject_reason ?? "");
    return { tone: "error", title: e.title, text: `${e.message} ${e.fix}` };
  }
  if (o.status === "filled") return { tone: "ok", title: R.filledTitle, text: R.filled(did, formatInt(filled), sym, at) };
  if (o.status === "partially_filled") {
    return { tone: "ok", title: R.partTitle,
             text: R.part(did, formatInt(filled), sym, at, formatInt(o.leaves_qty ?? 0), px(o.price ?? 0), ref) };
  }
  if (o.status === "open") {
    return { tone: "info", title: R.openTitle,
             text: R.open(o.side ?? "", formatInt(o.qty ?? 0), sym, px(o.price ?? 0), ref) };
  }
  if (o.status === "cancelled") {
    const c = explainCancel(o.cancel_reason ?? "unfilled");
    const head = filled ? R.cancelledAfter(did, formatInt(filled), sym, at) : R.nothingTraded;
    return { tone: filled ? "ok" : "info", title: c.title, text: `${head}${c.message}` };
  }
  return { tone: "info", title: o.status, text: R.other(ref, o.status) };
}
