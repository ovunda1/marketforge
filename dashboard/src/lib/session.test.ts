import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { isAuthError, loadSession, storeSession } from "./session";

function fakeStorage() {
  const data = new Map<string, string>();
  return { getItem: vi.fn((k: string) => data.get(k) ?? null), setItem: vi.fn((k: string, v: string) => void data.set(k, v)),
           removeItem: vi.fn((k: string) => void data.delete(k)), data };
}

afterEach(() => vi.unstubAllGlobals());

describe("the API key's home", () => {
  it("is sessionStorage, never localStorage", () => {
    const session = fakeStorage(), local = fakeStorage();
    vi.stubGlobal("window", { sessionStorage: session, localStorage: local });
    storeSession({ accountId: 5, apiKey: "mf_k", name: "p", practice: true });
    expect(loadSession()).toEqual({ accountId: 5, apiKey: "mf_k", name: "p", practice: true });
    expect(local.setItem).not.toHaveBeenCalled();
    expect(local.getItem).not.toHaveBeenCalled();
    storeSession(null);
    expect(loadSession()).toBeNull();
  });
  it("falls back to memory when storage is blocked", () => {
    vi.stubGlobal("window", { get sessionStorage() { throw new Error("blocked"); } });
    expect(() => storeSession({ accountId: 1, apiKey: "k", name: "n", practice: true })).not.toThrow();
    expect(loadSession()).toBeNull();
  });
  it("a 401 means the key is no good", () => {
    expect(isAuthError(new ApiError(401, "UNAUTHORIZED", "x"))).toBe(true);
    expect(isAuthError(new ApiError(429, "RATE_LIMITED", "x"))).toBe(false);
  });
});
