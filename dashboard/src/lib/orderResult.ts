// What happened to a placed order, as a sentence (M11), from the gateway's OrderOut.
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
  const did = o.side === "sell" ? "Sold" : "Bought";
  const fills = o.fills ?? [];
  const filled = fills.reduce((n, f) => n + f.qty, 0);
  const at = fills.map((f) => `${formatInt(f.qty)} at ${px(f.price)}`).join(", ");
  const sym = o.symbol ?? "";
  const ref = `order #${o.order_id}`;
  if (o.status === "rejected") {
    const e = explainReject(o.reject_reason ?? "");
    return { tone: "error", title: e.title, text: `${e.message} ${e.fix}` };
  }
  if (o.status === "filled") return { tone: "ok", title: "Filled", text: `${did} ${formatInt(filled)} ${sym}: ${at}.` };
  if (o.status === "partially_filled") {
    return { tone: "ok", title: "Partly filled",
             text: `${did} ${formatInt(filled)} ${sym} now (${at}); the other ${formatInt(o.leaves_qty ?? 0)} wait in the ` +
               `book at ${px(o.price ?? 0)} (${ref}).` };
  }
  if (o.status === "open") {
    return { tone: "info", title: "In the book",
             text: `Your order to ${o.side} ${formatInt(o.qty ?? 0)} ${sym} at ${px(o.price ?? 0)} is waiting in the book (${ref}).` };
  }
  if (o.status === "cancelled") {
    const c = explainCancel(o.cancel_reason ?? "unfilled");
    const head = filled ? `${did} ${formatInt(filled)} ${sym} (${at}). ` : "Nothing traded. ";
    return { tone: filled ? "ok" : "info", title: c.title, text: `${head}${c.message}` };
  }
  return { tone: "info", title: o.status, text: `${ref}: ${o.status}.` };
}
