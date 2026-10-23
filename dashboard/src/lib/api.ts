// The gateway over fetch (M11). Paths are the gateway's own: nginx (or the Vite dev
// server) serves it under /api. Every body goes through parseJson, which refuses to round
// a big integer. Errors keep the gateway's schema ({"error": {code, message, ...}}).

import { parseJson } from "./json";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string,
              public readonly body: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
  }
  get reason(): string | undefined {
    return typeof this.body.reason === "string" ? this.body.reason : undefined;
  }
  get details(): { field?: string; loc?: unknown[]; message?: string; msg?: string }[] {
    return Array.isArray(this.body.details) ? (this.body.details as never) : [];
  }
}

export interface RequestRecord {
  method: string;
  path: string;
  status: number;
  ms: number;
  requestId: string | null;
  worker: string | null;
}

// The last few requests, for "Show technical details".
const recent: RequestRecord[] = [];
const listeners = new Set<() => void>();
export function recentRequests(): RequestRecord[] {
  return recent;
}
export function onRequest(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export const API_BASE = "/api";

export async function api<T>(path: string, opts: { method?: string; body?: unknown; key?: string | null;
                                                 signal?: AbortSignal } = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.key) headers["X-API-Key"] = opts.key;
  const start = performance.now();
  const res = await fetch(API_BASE + path, { method, headers, signal: opts.signal,
                                             body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
  const text = await res.text();
  recent.unshift({ method, path, status: res.status, ms: performance.now() - start,
                   requestId: res.headers.get("X-Request-ID"), worker: res.headers.get("X-Worker") });
  recent.length = Math.min(recent.length, 8);
  listeners.forEach((fn) => fn());
  let data: unknown = null;
  if (text) {
    try {
      data = parseJson(text);
    } catch (e) {
      if (res.ok) throw e; // an unsafe integer in a success body: loud
      data = null;
    }
  }
  if (!res.ok) {
    const err = (data as { error?: Record<string, unknown> } | null)?.error;
    const code = typeof err?.code === "string" ? err.code : res.status === 404 ? "NOT_FOUND" : "ERROR";
    const message = typeof err?.message === "string" ? err.message : `HTTP ${res.status}`;
    throw new ApiError(res.status, code, message, err ?? {});
  }
  return data as T;
}

/** ws://host/ws/... for a gateway WebSocket path, on the page's own origin. */
export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}
