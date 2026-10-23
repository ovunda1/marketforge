// The trading session (M11): the API key lives in memory and in sessionStorage only, never
// localStorage. sessionStorage survives a reload but not closing the tab, and is not
// shared with other tabs. A practice account comes from POST /practice-accounts (demo
// mode only); a pasted key is checked with GET /accounts/me.

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { api, ApiError } from "./api";

export interface Session {
  accountId: number;
  apiKey: string;
  name: string;
  practice: boolean;
}

const STORAGE_KEY = "mf.session";

export function loadSession(): Session | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null; // storage blocked: memory only
  }
}

export function storeSession(s: Session | null): void {
  try {
    if (s) window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage blocked: the session still works for this page
  }
}

interface SessionApi {
  session: Session | null;
  startPractice: () => Promise<Session>;
  adoptKey: (key: string) => Promise<Session>;
  end: () => void;
}

const Ctx = createContext<SessionApi | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(loadSession);
  const set = useCallback((s: Session | null) => {
    storeSession(s);
    setSession(s);
  }, []);
  const startPractice = useCallback(async () => {
    const r = await api<{ account_id: number; api_key: string; name: string }>("/practice-accounts", { method: "POST" });
    const s = { accountId: r.account_id, apiKey: r.api_key, name: r.name, practice: true };
    set(s);
    return s;
  }, [set]);
  const adoptKey = useCallback(async (key: string) => {
    const me = await api<{ account_id: number; practice: boolean }>("/accounts/me", { key: key.trim() });
    const s = { accountId: me.account_id, apiKey: key.trim(), name: `account ${me.account_id}`, practice: me.practice };
    set(s);
    return s;
  }, [set]);
  const end = useCallback(() => set(null), [set]);
  const value = useMemo(() => ({ session, startPractice, adoptKey, end }), [session, startPractice, adoptKey, end]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

/** A 401 means the key is gone (account removed, wrong stack): forget it. */
export function isAuthError(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}
