// Live market (M11): the status strip, the price ladder and the order form as the focal
// points, the trade tape, and your account once you trade. Symbols are fictional.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { AccountPanel } from "../components/AccountPanel";
import { ErrorNote } from "../components/ErrorNote";
import { Info } from "../components/Info";
import { Ladder } from "../components/Ladder";
import { OrderForm } from "../components/OrderForm";
import { StatusStrip } from "../components/StatusStrip";
import { Tape } from "../components/Tape";
import { TechDetails } from "../components/TechDetails";
import { seenTour, Tour } from "../components/Tour";
import { ExecutionsFeed } from "../lib/feeds";
import { useInstruments, useMarketFeed } from "../lib/hooks";
import { formatTicks } from "../lib/money";
import { explain } from "../lib/rejects";
import { useSession } from "../lib/session";
import { useTech } from "../lib/tech";

export function Market() {
  const { symbol: param } = useParams();
  const instruments = useInstruments();
  const list = instruments.data ?? [];
  const symbol = param ?? null;
  const instrument = list.find((i) => i.symbol === symbol) ?? null;
  const feed = useMarketFeed(instrument ? instrument.symbol : null);
  const { session } = useSession();
  const { show } = useTech();
  const [picked, setPicked] = useState<{ price: number; n: number } | null>(null);
  const [refresh, setRefresh] = useState<{ seqNo: number | null; n: number }>({ seqNo: null, n: 0 });
  const [mine, setMine] = useState<Set<number>>(new Set());
  const [executions, setExecutions] = useState<ExecutionsFeed | null>(null);
  const [tour, setTour] = useState(false);
  const [, setExecState] = useState(0);
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Execution reports (fills, cancels by others' trades): refresh the account panel, batched.
  useEffect(() => {
    if (!session) return;
    const ex = new ExecutionsFeed(session.apiKey, (r) => {
      if (pending.current) clearTimeout(pending.current);
      pending.current = setTimeout(() => setRefresh((x) => ({ seqNo: r.symbol === symbol ? r.seq_no : null, n: x.n + 1 })), 150);
    }, () => setExecState((n) => n + 1));
    setExecutions(ex);
    ex.start();
    return () => {
      ex.stop();
      setExecutions(null);
    };
  }, [session, symbol]);

  // The tour starts once, shortly after the book first arrives; a later resync (the feed
  // flipping to unsynced and back) must not cancel or restart it.
  const synced = feed?.client.synced ?? false;
  const tourTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (synced && tourTimer.current === null && !seenTour()) tourTimer.current = setTimeout(() => setTour(true), 400);
  }, [synced]);
  useEffect(() => () => {
    if (tourTimer.current) clearTimeout(tourTimer.current);
    tourTimer.current = null; // StrictMode remounts: arm again
  }, []);

  const onPick = useCallback((price: number) => setPicked((p) => ({ price, n: (p?.n ?? 0) + 1 })), []);
  const onPlaced = useCallback((seqNo: number | null) => setRefresh((x) => ({ seqNo, n: x.n + 1 })), []);

  if (instruments.error) {
    return <div className="page"><ErrorNote e={explain(instruments.error)} /></div>;
  }
  if (!param && list.length) return <Navigate to={`/market/${list[0]!.symbol}`} replace />;
  if (param && list.length && !instrument) {
    return <div className="page"><p>No symbol {param} here. <Link to="/market">See the listed symbols</Link>.</p></div>;
  }
  const tick = instrument?.tick_size ?? "0.01";
  const bestBid = feed?.client.synced ? feed.client.bids()[0]?.[0] ?? null : null;
  const bestAsk = feed?.client.synced ? feed.client.asks()[0]?.[0] ?? null : null;
  const last = feed?.lastPrice ?? null;

  return (
    <div className="page wide">
      <StatusStrip feed={feed} />
      {show && <TechDetails feed={feed} executions={executions} />}
      <div className="ticker">
        <nav className="symbols" aria-label="Symbols">
          {list.map((i) => (
            <Link key={i.symbol} to={`/market/${i.symbol}`} aria-current={i.symbol === symbol ? "page" : undefined}>{i.symbol}</Link>
          ))}
        </nav>
        {instrument && <span className="muted">{instrument.name}</span>}
        <span className="chip">fictional company · simulated prices</span>
        <div className="last">
          <span className="muted">Last <Info term="lastPrice" label="last price" /></span>
          <span className="last-v num" data-testid="last-price">{last !== null ? formatTicks(last, tick) : "–"}</span>
        </div>
        <button type="button" className="btn outline small tour-btn" onClick={() => setTour(true)}>Take the 30-second tour</button>
      </div>
      <h1 className="sr-only">Live market: {symbol}</h1>
      <div className="grid">
        <Ladder feed={feed} tickSize={tick} onPick={onPick} mine={mine} />
        <div className="stack">
          {instrument && <OrderForm symbol={instrument.symbol} tickSize={tick} bestBid={bestBid} bestAsk={bestAsk}
                                    picked={picked} onPlaced={onPlaced} />}
          <Tape feed={feed} tickSize={tick} />
        </div>
      </div>
      {session && symbol && <AccountPanel symbol={symbol} instruments={list} refresh={refresh} onOpenPrices={setMine} />}
      <Tour open={tour} onClose={() => setTour(false)} />
    </div>
  );
}
