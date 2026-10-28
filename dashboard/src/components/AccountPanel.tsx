// Your account (M11, M11b): open orders (with cancel), fills and positions, as tabs in one panel.
// Refreshed after each order you place and on every execution report; reads pass
// min_seq_no so the projection has caught up with what you just did.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { api } from "../lib/api";
import type { Instrument } from "../lib/hooks";
import { formatInt, formatTicks } from "../lib/money";
import type { OrderOut } from "../lib/orderResult";
import { explain, type Explained } from "../lib/rejects";
import { isAuthError, useSession } from "../lib/session";
import { useTech } from "../lib/tech";
import { account as C, market } from "../copy";
import { ErrorNote } from "./ErrorNote";
import { Panel } from "./Panel";
import { Term } from "./Term";

interface FillRow { symbol: string; trade_id: number; order_id: number; side: string; liquidity: string; price: number;
                    qty: number; seq_no: number }
interface Position { symbol: string; qty: number; cash: string; mark: number | null; pnl: string | null;
                     open_buy_qty: number; open_sell_qty: number }

const TABS = ["orders", "fills", "positions"] as const;
type Tab = (typeof TABS)[number];

export function AccountPanel({ n, symbol, instruments, refresh, onOpenPrices }: {
  n: string; symbol: string; instruments: Instrument[]; refresh: { seqNo: number | null; n: number };
  onOpenPrices: (prices: Set<number>) => void;
}) {
  const { session, end } = useSession();
  const { show } = useTech();
  const [tab, setTab] = useState<Tab>("orders");
  const [orders, setOrders] = useState<OrderOut[]>([]);
  const [fills, setFills] = useState<FillRow[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [error, setError] = useState<Explained | null>(null);
  const [cancelling, setCancelling] = useState<number | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const tick = (s: string) => instruments.find((i) => i.symbol === s)?.tick_size ?? "0.01";

  const load = useCallback(async (seqNo: number | null) => {
    if (!session) return;
    const wait = seqNo ? `&symbol=${symbol}&min_seq_no=${seqNo}` : "";
    try {
      const [o, f, p] = await Promise.all([
        api<{ orders: OrderOut[] }>(`/orders?status=open${wait}`, { key: session.apiKey }),
        api<{ fills: FillRow[] }>(`/fills?limit=50${wait}`, { key: session.apiKey }),
        api<{ positions: Position[] }>(`/accounts/${session.accountId}/positions${seqNo ? `?symbol=${symbol}&min_seq_no=${seqNo}` : ""}`,
                                       { key: session.apiKey })]);
      setOrders(o.orders);
      setFills(f.fills);
      setPositions(p.positions);
      setError(null);
      onOpenPrices(new Set(o.orders.filter((x) => x.symbol === symbol).map((x) => x.price ?? 0)));
    } catch (e) {
      if (isAuthError(e)) end();
      setError(explain(e));
    }
  }, [session, symbol, end, onOpenPrices]);

  useEffect(() => {
    void load(refresh.seqNo);
  }, [load, refresh]);

  const cancel = async (o: OrderOut) => {
    if (!session) return;
    setCancelling(o.order_id);
    try {
      const r = await api<OrderOut>(`/orders/${o.order_id}`, { method: "DELETE", key: session.apiKey });
      await load(r.seq_no ?? null);
    } catch (e) {
      setError(explain(e));
      await load(null);
    } finally {
      setCancelling(null);
    }
  };

  const onKey = (e: KeyboardEvent, i: number) => {
    const next = e.key === "ArrowRight" ? (i + 1) % TABS.length : e.key === "ArrowLeft" ? (i + TABS.length - 1) % TABS.length : -1;
    if (next >= 0) {
      setTab(TABS[next]!);
      tabRefs.current[next]?.focus();
    }
  };

  if (!session) return null;
  const K = C.cols;
  const tablist = (
    <div className="tabs" role="tablist" aria-label={C.tabsLabel}>
      {TABS.map((id, i) => (
        <button key={id} type="button" role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls={`tp-${id}`}
                tabIndex={tab === id ? 0 : -1} ref={(el) => { tabRefs.current[i] = el; }}
                onClick={() => setTab(id)} onKeyDown={(e) => onKey(e, i)}>
          {C.tabs[id]}{id === "orders" && orders.length ? ` (${orders.length})` : ""}
        </button>
      ))}
    </div>
  );
  return (
    <Panel id="account" n={n} title={market.sections.account} right={tablist}>
      {error && <div className="panel-b"><ErrorNote e={error} /></div>}
      <div role="tabpanel" id={`tp-${tab}`} aria-labelledby={`tab-${tab}`} className="scroll-x" data-testid={`account-${tab}`}>
        {tab === "orders" && (orders.length === 0 ? <p className="empty">{C.noOrders}</p> : (
          <table className="data">
            <thead><tr><th scope="col">{K.symbol}</th><th scope="col">{K.side}</th><th scope="col" className="num">{K.price}</th>
              <th scope="col" className="num">{K.open}</th><th scope="col" className="num">{K.filled}</th>
              {show && <th scope="col" className="num">{K.orderId}</th>}<th scope="col"><span className="sr-only">{K.cancel}</span></th></tr></thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td>{o.symbol}</td>
                  <td><span className={`side-word ${o.side}`}>{o.side === "buy" ? C.buy : C.sell}</span></td>
                  <td className="num">{formatTicks(o.price ?? 0, tick(o.symbol ?? ""))}</td>
                  <td className="num">{formatInt(o.leaves_qty ?? 0)}</td>
                  <td className="num">{formatInt(o.cum_qty ?? 0)}</td>
                  {show && <td className="num muted mono">{o.order_id}</td>}
                  <td className="num">
                    <button type="button" className="btn danger small" onClick={() => cancel(o)} disabled={cancelling === o.order_id}
                            aria-label={C.cancelLabel(o.side ?? "", formatInt(o.leaves_qty ?? 0), o.symbol ?? "",
                                                      formatTicks(o.price ?? 0, tick(o.symbol ?? "")))}>
                      {cancelling === o.order_id ? C.cancelling : C.cancel}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
        {tab === "fills" && (fills.length === 0 ? <p className="empty">{C.noFills}</p> : (
          <table className="data">
            <thead><tr><th scope="col">{K.symbol}</th><th scope="col">{K.side}</th><th scope="col" className="num">{K.price}</th>
              <th scope="col" className="num">{K.qty}</th><th scope="col"><Term k="liquidity">{K.liquidity}</Term></th>
              {show && <th scope="col" className="num">{K.seqNo}</th>}</tr></thead>
            <tbody>
              {fills.map((f) => (
                <tr key={`${f.symbol}-${f.trade_id}-${f.order_id}`}>
                  <td>{f.symbol}</td>
                  <td><span className={`side-word ${f.side}`}>{f.side === "buy" ? C.bought : C.sold}</span></td>
                  <td className="num">{formatTicks(f.price, tick(f.symbol))}</td>
                  <td className="num">{formatInt(f.qty)}</td>
                  <td>{f.liquidity === "maker" ? C.maker : C.taker}</td>
                  {show && <td className="num muted mono">{formatInt(f.seq_no)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        ))}
        {tab === "positions" && (positions.length === 0 ? <p className="empty">{C.noPositions}</p> : (
          <table className="data">
            <thead><tr><th scope="col">{K.symbol}</th><th scope="col" className="num">{K.shares}</th>
              <th scope="col" className="num">{K.last}</th><th scope="col" className="num"><Term k="pnl">{K.pnl}</Term></th></tr></thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.symbol}>
                  <td>{p.symbol}</td>
                  <td className="num">{formatInt(p.qty)}{p.qty < 0 ? C.short : ""}</td>
                  <td className="num">{p.mark !== null ? formatTicks(p.mark, tick(p.symbol)) : "–"}</td>
                  <td className="num" data-testid={`pnl-${p.symbol}`}>
                    {p.pnl !== null ? `${p.pnl.startsWith("-") ? "−" : ""}$${formatTicks(p.pnl.replace("-", ""), tick(p.symbol))}` : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </div>
    </Panel>
  );
}
