// "Show technical details" (M11): one switch for the whole app. Sequence numbers beyond
// the status strip, error codes, request ids and connection internals appear only when on.
// Remembered for this tab (sessionStorage); it holds no secret.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

const KEY = "mf.tech";
const Ctx = createContext<{ show: boolean; toggle: () => void }>({ show: false, toggle: () => {} });

function initial(): boolean {
  try {
    return window.sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function TechProvider({ children }: { children: ReactNode }) {
  const [show, setShow] = useState(initial);
  const toggle = useCallback(() => {
    setShow((s) => {
      try {
        window.sessionStorage.setItem(KEY, s ? "0" : "1");
      } catch {
        // not remembered
      }
      return !s;
    });
  }, []);
  const value = useMemo(() => ({ show, toggle }), [show, toggle]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useTech = () => useContext(Ctx);
