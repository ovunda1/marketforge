// Order entry (M11): a big Buy/Sell toggle (real radio buttons: arrow keys work), the
// price pre-filled from the other side of the book, and a sentence saying what the order
// will do before it is placed. Typed text becomes integer ticks through money.ts (no
// floating point); a price off the tick grid shows the two nearest valid prices.
import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import { formatTicks, InputError, parsePrice, parseQty } from "../lib/money";
import { describeResult, type OrderOut } from "../lib/orderResult";
import { summarize, type OrderType, type Side, type Tif } from "../lib/orderSummary";
import { explain, type Explained } from "../lib/rejects";
import { isAuthError, useSession } from "../lib/session";
import { useTech } from "../lib/tech";
import { ErrorNote } from "./ErrorNote";
import { Info } from "./Info";
import { Panel } from "./Panel";
import { TryTrading } from "./TryTrading";

const TIFS: { value: Tif; label: string }[] = [
  { value: "gtc", label: "Good till cancelled" }, { value: "day", label: "Day (cancelled at the close)" },
  { value: "ioc", label: "Immediate or cancel" }, { value: "fok", label: "Fill or kill" }];

function parsed<T>(fn: () => T): { value: T | null; error: InputError | null } {
  try {
    return { value: fn(), error: null };
  } catch (e) {
    if (e instanceof InputError) return { value: null, error: e };
    throw e;
  }
}

export function OrderForm({ symbol, tickSize, bestBid, bestAsk, picked, onPlaced }: {
  symbol: string; tickSize: string; bestBid: number | null; bestAsk: number | null;
  picked: { price: number; n: number } | null; onPlaced: (seqNo: number | null) => void;
}) {
  const { session, end } = useSession();
  const { show } = useTech();
  const [side, setSide] = useState<Side>("buy");
  const [type, setType] = useState<OrderType>("limit");
  const [tif, setTif] = useState<Tif>("gtc");
  const [postOnly, setPostOnly] = useState(false);
  const [qtyText, setQtyText] = useState("10");
  const [priceText, setPriceText] = useState("");
  const [priceTouched, setPriceTouched] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReturnType<typeof describeResult> | null>(null);
  const [error, setError] = useState<Explained | null>(null);
  const priceRef = useRef<HTMLInputElement>(null);

  // Pre-fill: the best price on the other side (what a buy would pay now), until the
  // person types their own; a new side or symbol pre-fills again.
  const prefill = side === "buy" ? bestAsk ?? bestBid : bestBid ?? bestAsk;
  useEffect(() => {
    setPriceTouched(false);
  }, [side, symbol]);
  useEffect(() => {
    if (!priceTouched && prefill !== null) setPriceText(fmt(prefill));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill, priceTouched, tickSize]);
  useEffect(() => {
    if (picked) {
      setPriceText(fmt(picked.price));
      setPriceTouched(true);
      setType("limit");
      priceRef.current?.focus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [picked]);
  function fmt(t: number): string {
    return formatTicks(t, tickSize);
  }

  const qty = parsed(() => parseQty(qtyText));
  const price = type === "limit" ? parsed(() => parsePrice(priceText, tickSize)) : { value: null, error: null };
  const effectiveTif: Tif = type === "market" ? "ioc" : tif;
  const effectivePostOnly = type === "limit" && (tif === "gtc" || tif === "day") && postOnly;
  const sentence = summarize({ symbol, tickSize, side, type, tif: effectiveTif, price: price.value, qty: qty.value,
                               postOnly: effectivePostOnly }, { bestBid, bestAsk });
  const valid = qty.value !== null && (type === "market" || price.value !== null);

  const place = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setSubmitted(true);
    setResult(null);
    setError(null);
    if (!valid || !session) return;
    setBusy(true);
    try {
      const order = await api<OrderOut>("/orders", { method: "POST", key: session.apiKey, body: {
        symbol, side, type, tif: effectiveTif, price: type === "market" ? 0 : price.value, qty: qty.value,
        post_only: effectivePostOnly, client_order_id: crypto.randomUUID() } });
      setResult(describeResult(order, tickSize));
      onPlaced(order.seq_no ?? null);
    } catch (e) {
      if (isAuthError(e)) end();
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel id="order" focal title="New order" right={<span className="src">{symbol} · tick {tickSize}</span>}>
      <form className="panel-b" onSubmit={place} noValidate aria-describedby="order-summary">
        <fieldset className="toggle" id="side">
          <legend className="sr-only">Side</legend>
          <label className="buy">
            <input type="radio" name="side" value="buy" checked={side === "buy"} onChange={() => setSide("buy")} />
            <span>▲ Buy</span>
          </label>
          <label className="sell">
            <input type="radio" name="side" value="sell" checked={side === "sell"} onChange={() => setSide("sell")} />
            <span>▼ Sell</span>
          </label>
        </fieldset>

        <fieldset>
          <legend>Order type <Info term="orderType" label="order type" /></legend>
          <div className="seg">
            {(["limit", "market"] as const).map((t) => (
              <label key={t}>
                <input type="radio" name="type" value={t} checked={type === t} onChange={() => setType(t)} />
                <span>{t === "limit" ? "Limit" : "Market"}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="two">
          <div className="field">
            <label className="label" htmlFor="qty">Quantity <Info term="qty" label="quantity" /></label>
            <input id="qty" type="text" inputMode="numeric" autoComplete="off" value={qtyText}
                   aria-invalid={submitted && qty.error ? true : undefined} aria-describedby="qty-err"
                   onChange={(e) => setQtyText(e.target.value)} />
            <span id="qty-err" className="err" role={submitted && qty.error ? "alert" : undefined}>
              {submitted || qtyText ? qty.error?.message : ""}
            </span>
          </div>
          {type === "limit" ? (
            <div className="field">
              <label className="label" htmlFor="price">Limit price ($) <Info term="limitPrice" label="limit price" /></label>
              <input id="price" ref={priceRef} type="text" inputMode="decimal" autoComplete="off" value={priceText}
                     aria-invalid={price.error && (submitted || priceTouched) ? true : undefined}
                     aria-describedby="price-hint"
                     onChange={(e) => { setPriceText(e.target.value); setPriceTouched(true); }} />
              <span id="price-hint" className={price.error && (submitted || priceTouched) ? "err" : "hint"}>
                {price.error && (submitted || priceTouched) ? price.error.message
                  : priceTouched ? `Steps of ${tickSize}.` : "Pre-filled with the best price on the other side."}
              </span>
            </div>
          ) : (
            <div className="field">
              <span className="label">Price</span>
              <p className="hint" style={{ margin: "8px 0 0" }}>Best available now</p>
            </div>
          )}
        </div>

        {type === "limit" && (
          <div className="two">
            <div className="field">
              <label className="label" htmlFor="tif">Time in force <Info term="tif" label="time in force" /></label>
              <select id="tif" value={tif} onChange={(e) => setTif(e.target.value as Tif)}>
                {TIFS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div className="field" style={{ alignSelf: "end" }}>
              <label className="check">
                <input type="checkbox" checked={effectivePostOnly} disabled={tif === "ioc" || tif === "fok"}
                       onChange={(e) => setPostOnly(e.target.checked)} />
                Post-only <Info term="postOnly" label="post-only" />
              </label>
            </div>
          </div>
        )}

        <p className="summary" id="order-summary" aria-live="polite" data-testid="order-summary">{sentence}</p>

        {session && (
          <button type="submit" className={`btn submit ${side}`} disabled={busy} data-testid="place-order">
            {busy ? "Placing…" : side === "buy" ? "▲ Place buy order" : "▼ Place sell order"}
          </button>
        )}

        <div aria-live="polite" style={{ marginTop: 12 }}>
          {result && (
            <div className={`note ${result.tone === "error" ? "error" : result.tone === "ok" ? "ok" : "notice"}`}
                 role="status" data-testid="order-result">
              <strong>{result.title}</strong>
              <p>{result.text}</p>
            </div>
          )}
          {error && <ErrorNote e={error} />}
        </div>
        {show && session && <p className="src">account {session.accountId} · {session.practice ? "practice" : "key"}</p>}
      </form>
      {!session && <div className="panel-b" style={{ paddingTop: 0 }}><TryTrading /></div>}
    </Panel>
  );
}
