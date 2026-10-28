// "Try trading" (M11): one click makes a practice account (demo mode only). The key stays in
// this tab (memory + sessionStorage). Engineers can paste an existing key instead.
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { tryTrading as C } from "../copy";
import { useDeployConfig } from "../lib/hooks";
import { explain, type Explained } from "../lib/rejects";
import { useSession } from "../lib/session";
import { ErrorNote } from "./ErrorNote";

export function TryTrading({ compact = false }: { compact?: boolean }) {
  const { startPractice, adoptKey } = useSession();
  const config = useDeployConfig();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Explained | null>(null);
  const [key, setKey] = useState("");
  const demo = config.data?.demo_mode ?? false;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await startPractice();
      // From a page without trading on it, go where the trading is.
      if (compact && !pathname.startsWith("/market") && !pathname.startsWith("/backtests/new")) navigate("/market");
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  };
  const paste = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await adoptKey(key);
      setKey("");
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  };

  if (compact) {
    return demo ? (
      <span className="try-compact">
        <button type="button" className="btn outline small try-btn" onClick={start} disabled={busy} aria-busy={busy}
                data-testid="try-trading">
          {busy ? C.creating : C.button}
        </button>
        {error && (
          <span className="nav-error">
            <ErrorNote e={error} />
            <button type="button" className="linkish" onClick={() => setError(null)}>{C.dismiss}</button>
          </span>
        )}
      </span>
    ) : null;
  }
  return (
    <div className="try">
      {demo && (
        <>
          <p>{C.intro}</p>
          <button type="button" className="btn primary" onClick={start} disabled={busy} data-testid="try-trading-main">
            {busy ? C.mainBusy : C.main}
          </button>
        </>
      )}
      <form className="key-form" onSubmit={paste} aria-label={C.keyForm}>
        <label className="sr-only" htmlFor="api-key">{C.keyLabel}</label>
        <input id="api-key" type="password" autoComplete="off" placeholder={demo ? C.keyPlaceholderDemo : C.keyPlaceholder}
               value={key} onChange={(e) => setKey(e.target.value)} />
        <button type="submit" className="btn outline small" disabled={busy || !key.trim()}>{C.useKey}</button>
      </form>
      {error && <ErrorNote e={error} />}
    </div>
  );
}
