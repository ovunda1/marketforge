// Live feeds over the gateway's WebSockets (M11).
//
// MarketFeed: /ws/marketdata/{symbol} through BookClient (the same protocol as
// services/client.py). A gap or a new epoch (the matcher restarted) means reconnect for a
// fresh snapshot; after "resnapshot" the server sends the snapshot by itself; a dropped
// connection reconnects with backoff. A ping every few seconds measures the round trip
// to the gateway, queueing included. The tape starts from GET /trades and continues
// with the trades in applied updates.
//
// ExecutionsFeed: /ws/executions for one account; the key goes in the first message
// (browsers cannot set WebSocket headers).

import { api, wsUrl } from "./api";
import { BookClient, type MdMessage } from "./bookClient";
import { parseJson } from "./json";

export interface Trade {
  trade_id: number;
  seq_no: number;
  price: number;
  qty: number;
  aggressor_side: "buy" | "sell";
}

export type FeedStatus = "connecting" | "live" | "resyncing" | "reconnecting" | "offline";

export interface FeedStats {
  messages: number;
  gaps: number;
  resnapshots: number;
  reconnects: number;
  lastMessageAt: number | null;
  closeCode: number | null;
}

const PING_EVERY_MS = 5000;
const TAPE = 40;

export class MarketFeed {
  client: BookClient;
  trades: Trade[] = [];
  status: FeedStatus = "connecting";
  rttMs: number | null = null;
  stats: FeedStats = { messages: 0, gaps: 0, resnapshots: 0, reconnects: 0, lastMessageAt: null, closeCode: null };
  private ws: WebSocket | null = null;
  private stopped = false;
  private backoff = 500;
  private pingId = 0;
  private pings = new Map<number, number>();
  private timers: ReturnType<typeof setTimeout>[] = [];
  private pinger: ReturnType<typeof setInterval> | null = null;
  private frame: number | null = null;

  constructor(public readonly symbol: string, private readonly onChange: () => void) {
    this.client = new BookClient(symbol);
  }

  start(): void {
    this.connect();
    void this.loadTape();
    this.pinger = setInterval(() => this.ping(), PING_EVERY_MS);
  }

  stop(): void {
    this.stopped = true;
    this.timers.forEach(clearTimeout);
    if (this.pinger) clearInterval(this.pinger);
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.ws?.close(1000);
  }

  get lastPrice(): number | null {
    return this.trades[0]?.price ?? null;
  }

  private changed(): void {
    // Many updates a second: render at most once a frame.
    if (this.frame === null) {
      this.frame = requestAnimationFrame(() => {
        this.frame = null;
        this.onChange();
      });
    }
  }

  private async loadTape(): Promise<void> {
    try {
      const r = await api<{ trades: Trade[] }>(`/trades/${encodeURIComponent(this.symbol)}?limit=${TAPE}`);
      this.addTrades(r.trades, true);
    } catch {
      // the tape fills from live updates
    }
  }

  private addTrades(trades: Trade[], older: boolean): void {
    const seen = new Set(this.trades.map((t) => t.trade_id));
    const fresh = trades.filter((t) => !seen.has(t.trade_id));
    if (!fresh.length) return;
    const merged = older ? [...this.trades, ...fresh] : [...[...fresh].reverse(), ...this.trades];
    this.trades = merged.sort((a, b) => b.seq_no - a.seq_no || b.trade_id - a.trade_id).slice(0, TAPE);
    this.changed();
  }

  private connect(): void {
    if (this.stopped) return;
    const ws = new WebSocket(wsUrl(`/ws/marketdata/${encodeURIComponent(this.symbol)}`));
    this.ws = ws;
    ws.onopen = () => {
      this.backoff = 500;
      this.ping();
    };
    ws.onmessage = (ev) => this.handle(typeof ev.data === "string" ? ev.data : "");
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.stats.closeCode = ev.code;
      if (this.stopped) return;
      if (ev.code === 4404) {
        this.status = "offline";
        this.changed();
        return;
      }
      this.status = "reconnecting";
      this.stats.reconnects += 1;
      this.changed();
      this.timers.push(setTimeout(() => this.connect(), this.backoff));
      this.backoff = Math.min(this.backoff * 2, 5000);
    };
  }

  /** Close this socket and open a new one: the gateway starts every connection with a snapshot. */
  private resubscribe(): void {
    const old = this.ws;
    this.ws = null;
    old?.close(1000);
    this.status = "resyncing";
    this.connect();
  }

  private handle(text: string): void {
    const msg = parseJson<MdMessage & { id?: number; trades?: Trade[] }>(text);
    this.stats.messages += 1;
    this.stats.lastMessageAt = Date.now();
    if (msg.type === "pong") {
      const sent = this.pings.get(msg.id ?? -1);
      if (sent !== undefined) {
        this.rttMs = performance.now() - sent;
        this.pings.delete(msg.id!);
      }
      this.changed();
      return;
    }
    const before = this.client.mdSeq;
    const ok = this.client.onMessage(msg);
    if (msg.type === "snapshot") {
      if (this.client.resnapshots > this.stats.resnapshots) this.stats.resnapshots = this.client.resnapshots;
      this.status = "live";
    } else if (!ok) {
      if (msg.type === "resnapshot") this.status = "resyncing"; // the snapshot follows by itself
      else {
        this.stats.gaps += 1; // a gap or a new epoch
        this.resubscribe();
      }
    } else if (msg.type === "update" && this.client.mdSeq > before && msg.trades?.length) {
      this.addTrades(msg.trades, false);
    }
    this.changed();
  }

  private ping(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.pingId += 1;
    this.pings.set(this.pingId, performance.now());
    if (this.pings.size > 10) this.pings.delete(this.pings.keys().next().value!);
    this.ws.send(JSON.stringify({ type: "ping", id: this.pingId }));
  }
}

export interface ExecutionReport {
  type: "execution";
  symbol: string;
  seq_no: number;
  account_id: number;
  event: { type: string; order_id?: number; reason?: string; price?: number; qty?: number; [k: string]: unknown };
}

export class ExecutionsFeed {
  connected = false;
  private ws: WebSocket | null = null;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly apiKey: string, private readonly onReport: (r: ExecutionReport) => void,
              private readonly onState: () => void) {}

  start(): void {
    if (this.stopped) return;
    const ws = new WebSocket(wsUrl("/ws/executions"));
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ api_key: this.apiKey }));
    ws.onmessage = (ev) => {
      const msg = parseJson<{ type: string }>(String(ev.data));
      if (msg.type === "subscribed") {
        this.connected = true;
        this.onState();
      } else if (msg.type === "execution") this.onReport(msg as ExecutionReport);
    };
    ws.onclose = (ev) => {
      this.connected = false;
      this.onState();
      if (!this.stopped && ev.code !== 4401) this.timer = setTimeout(() => this.start(), 2000);
    };
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close(1000);
  }
}
