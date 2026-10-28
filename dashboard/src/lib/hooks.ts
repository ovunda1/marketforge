import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { api } from "./api";
import { MarketFeed } from "./feeds";

export interface Async<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]): Async<T> {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>(
    { data: null, error: null, loading: true });
  const [n, bump] = useReducer((x: number) => x + 1, 0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    const ctl = new AbortController();
    setState((s) => ({ ...s, loading: true }));
    fnRef.current(ctl.signal).then(
      (data) => !ctl.signal.aborted && setState({ data, error: null, loading: false }),
      (error) => !ctl.signal.aborted && setState((s) => ({ data: s.data, error, loading: false })));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return { ...state, reload: useCallback(() => bump(), []) };
}

export interface Instrument {
  symbol: string;
  tick_size: string;
  name: string;
}

let instrumentsCache: Promise<Instrument[]> | null = null;
export function useInstruments(): Async<Instrument[]> {
  return useAsync(() => {
    instrumentsCache ??= api<{ instruments: Instrument[] }>("/instruments").then((r) => r.instruments)
      .catch((e) => { instrumentsCache = null; throw e; });
    return instrumentsCache;
  }, []);
}

export interface DeployConfig {
  demo_mode: boolean;
  practice_backtest_cap: number;
  max_price_ticks: number;
  max_order_qty: number;
  version: string;
}

let configCache: Promise<DeployConfig> | null = null;
export function useDeployConfig(): Async<DeployConfig> {
  return useAsync(() => {
    configCache ??= api<DeployConfig>("/config").catch((e) => { configCache = null; throw e; });
    return configCache;
  }, []);
}

export function useMarketFeed(symbol: string | null): MarketFeed | null {
  const [feed, setFeed] = useState<MarketFeed | null>(null);
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    if (!symbol) return;
    const f = new MarketFeed(symbol, rerender);
    setFeed(f);
    f.start();
    return () => f.stop();
  }, [symbol]);
  return feed && feed.symbol === symbol ? feed : null;
}

/** Re-render every `ms` (for "3 s ago" style values). */
export function useTick(ms: number): void {
  const [, rerender] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    const t = setInterval(rerender, ms);
    return () => clearInterval(t);
  }, [ms]);
}
