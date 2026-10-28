// The backtest form (M11): strategy, parameters, date range, universe and costs, validated
// as you type against the data catalog (the same rules the gateway enforces), then queued.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ErrorNote } from "../components/ErrorNote";
import { Info } from "../components/Info";
import { Panel } from "../components/Panel";
import { TryTrading } from "../components/TryTrading";
import type { Term } from "../content/glossary";
import { api, ApiError } from "../lib/api";
import { DAYS_PER_YEAR, MAX_GRID, STRATEGIES, TEST_DAYS, tradingDays, TRAIN_YEARS, validate, type Catalog,
         type FieldError, type Form } from "../lib/backtestForm";
import { useAsync } from "../lib/hooks";
import { explain, type Explained } from "../lib/rejects";
import { isAuthError, useSession } from "../lib/session";
import { DataChip, STRATEGY_LABEL } from "./Backtests";

const PARAM_TERMS: Record<string, [string, Term]> = {
  lookback: ["Lookback (trading days)", "lookback"], vol_window: ["Volatility window (days)", "volWindow"],
  target_vol: ["Target volatility", "targetVol"], max_leverage: ["Max leverage", "maxLeverage"],
  lookback_months: ["Lookback (months)", "lookbackMonths"], skip_months: ["Skip (months)", "skipMonths"],
  quantile: ["Quantile traded", "quantile"],
};
const DEFAULTS: Record<string, Record<string, number[]>> = {
  tsmom: { lookback: [60, 120], vol_window: [20], target_vol: [0.1], max_leverage: [2.0] },
  xsmom: { lookback_months: [6, 12], skip_months: [1], quantile: [0.2] },
};
const fmtValue = (name: string, v: number) => (name === "target_vol" || name === "quantile" ? `${Math.round(v * 100)}%` : `${v}`);

export function NewBacktest() {
  const catalog = useAsync(() => api<Catalog>("/backtests/catalog"), []);
  const { session, end } = useSession();
  const navigate = useNavigate();
  const [form, setForm] = useState<Form | null>(null);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Explained | null>(null);
  const [serverErrors, setServerErrors] = useState<FieldError[]>([]);

  useEffect(() => {
    const c = catalog.data;
    if (!c || form) return;
    const ds = c.datasets.find((d) => d.real) ?? c.datasets[0]!;
    const symbols = ds.symbols.map((s) => s.symbol).slice(0, 10);
    setForm({ dataset: ds.id, strategy: "tsmom", params: DEFAULTS.tsmom!, start: ds.start, end: ds.end, symbols,
              train_years: 3, costs: true });
  }, [catalog.data, form]);

  const errors = useMemo(() => (form && catalog.data ? validate(form, catalog.data) : []), [form, catalog.data]);
  const shown = touched ? errors : errors.filter((e) => e.field === "range" || e.field === "params" || e.field === "train_years");
  const errFor = (field: string) => [...shown, ...serverErrors].filter((e) => e.field === field || e.field.startsWith(`${field}.`));

  if (catalog.error) return <div className="page"><ErrorNote e={explain(catalog.error)} /></div>;
  if (!form || !catalog.data) return <div className="page"><p className="muted">Loading the data catalog…</p></div>;
  const ds = catalog.data.datasets.find((d) => d.id === form.dataset)!;
  const spec = STRATEGIES[form.strategy]!;
  const grid = Object.keys(spec.params).reduce((n, k) => n * (form.params[k]?.length ?? 0), 1);
  const days = tradingDays(ds.calendar, form.start, form.end);
  const set = (patch: Partial<Form>) => {
    setForm({ ...form, ...patch });
    setServerErrors([]);
  };
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setTouched(true);
    setError(null);
    if (errors.length || !session) return;
    setBusy(true);
    try {
      const r = await api<{ id: string }>("/backtests", { method: "POST", key: session.apiKey, body: { form } });
      navigate(`/backtests/${r.id}`);
    } catch (e) {
      if (isAuthError(e)) end();
      if (e instanceof ApiError && e.code === "VALIDATION_ERROR") {
        setServerErrors(e.details.map((d) => ({ field: d.field ?? "dataset", message: d.message ?? "" })));
      }
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  };

  const Err = ({ field }: { field: string }) => {
    const list = errFor(field);
    return list.length ? <div className="err" id={`${field}-err`} role="alert">{list.map((e) => <div key={e.field + e.message}>{e.message}</div>)}</div> : null;
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Run a backtest</h1>
        <p>Pick a strategy and the history to test it on. The run is a walk-forward: it learns its settings on a few
          years, is tested on the next year it hasn't seen, and rolls forward. Only the test years are reported.{" "}
          <Info term="walkForward" label="walk-forward" /></p>
      </div>
      <form onSubmit={submit} noValidate aria-label="Backtest settings">
        <Panel id="bt-data" title="Data and strategy">
          <div className="panel-b form-grid">
            <div className="field">
              <label className="label" htmlFor="dataset">Dataset</label>
              <select id="dataset" value={form.dataset} onChange={(e) => {
                const d = catalog.data!.datasets.find((x) => x.id === e.target.value)!;
                set({ dataset: d.id, start: d.start, end: d.end, symbols: d.symbols.map((s) => s.symbol).slice(0, 10) });
              }}>
                {catalog.data.datasets.map((d) => <option key={d.id} value={d.id}>{d.label} ({d.real ? "historical" : "synthetic"})</option>)}
              </select>
              <span className="hint">{ds.source}. <DataChip real={ds.real} /></span>
              <Err field="dataset" />
            </div>
            <fieldset>
              <legend>Strategy</legend>
              <div className="seg">
                {Object.entries(STRATEGIES).map(([k, s]) => (
                  <label key={k}><input type="radio" name="strategy" value={k} checked={form.strategy === k}
                                        onChange={() => set({ strategy: k, params: DEFAULTS[k]! })} /><span>{s.label}</span></label>
                ))}
              </div>
              <Err field="strategy" />
            </fieldset>
            {Object.entries(spec.params).map(([name, allowed]) => (
              <fieldset key={name}>
                <legend>{PARAM_TERMS[name]![0]} <Info term={PARAM_TERMS[name]![1]} label={PARAM_TERMS[name]![0]} /></legend>
                <div className="chips">
                  {allowed.map((v) => (
                    <label key={v}><input type="checkbox" checked={form.params[name]?.includes(v) ?? false}
                                          onChange={() => set({ params: { ...form.params, [name]: toggle(form.params[name] ?? [], v) } })} />
                      <span>{fmtValue(name, v)}</span></label>
                  ))}
                </div>
                <Err field={`params.${name}`} />
              </fieldset>
            ))}
            <div className="field">
              <span className="label">Combinations tried</span>
              <span className="num" data-testid="grid-size">{grid} of at most {MAX_GRID}</span>
              <span className="hint">Each is tried on every training window; the best one in training is tested.</span>
              <Err field="params" />
            </div>
          </div>
        </Panel>

        <Panel id="bt-range" title="History">
          <div className="panel-b form-grid">
            <div className="field">
              <label className="label" htmlFor="start">From</label>
              <input id="start" type="date" min={ds.start} max={ds.end} value={form.start}
                     aria-invalid={errFor("start").length ? true : undefined} onChange={(e) => set({ start: e.target.value })} />
              <Err field="start" />
            </div>
            <div className="field">
              <label className="label" htmlFor="end">To</label>
              <input id="end" type="date" min={ds.start} max={ds.end} value={form.end}
                     aria-invalid={errFor("end").length ? true : undefined} onChange={(e) => set({ end: e.target.value })} />
              <Err field="end" />
            </div>
            <div className="field">
              <label className="label" htmlFor="train">Training years <Info term="trainYears" label="training years" /></label>
              <select id="train" value={form.train_years} onChange={(e) => set({ train_years: Number.parseInt(e.target.value, 10) })}>
                {TRAIN_YEARS.map((y) => <option key={y} value={y}>{y} year{y > 1 ? "s" : ""}, then 1 year of testing</option>)}
              </select>
              <Err field="train_years" />
            </div>
            <div className="field">
              <span className="label">Trading days in range</span>
              <span className="num" data-testid="trading-days">{days} (at least {form.train_years * DAYS_PER_YEAR + TEST_DAYS} needed)</span>
              <span className="hint">Data available {ds.start} to {ds.end}.</span>
              <Err field="range" />
            </div>
          </div>
        </Panel>

        <Panel id="bt-universe" title={<>Universe <Info term="universe" label="the universe" /></>}
               right={<span className="src">{form.symbols.length} of {ds.symbols.length} · {ds.real ? "real tickers, historical data" : "fictional symbols"}</span>}>
          <div className="panel-b">
            <div className="chips" role="group" aria-label="Symbols">
              {ds.symbols.map((s) => (
                <label key={s.symbol} title={`${s.first} to ${s.last}`}>
                  <input type="checkbox" checked={form.symbols.includes(s.symbol)} onChange={() => set({ symbols: toggle(form.symbols, s.symbol) })} />
                  <span>{s.symbol}</span></label>
              ))}
            </div>
            <p style={{ marginTop: 10 }}>
              <button type="button" className="linkish" onClick={() => set({ symbols: ds.symbols.map((s) => s.symbol) })}>Select all</button>{" · "}
              <button type="button" className="linkish" onClick={() => set({ symbols: [] })}>Clear</button>
            </p>
            {ds.survivorship && <p className="hint">{ds.survivorship}</p>}
            <Err field="symbols" />
            <label className="check" style={{ marginTop: 12 }}>
              <input type="checkbox" checked={form.costs} onChange={(e) => set({ costs: e.target.checked })} />
              Include trading costs (commission, fees, spread, impact, financing) <Info term="costs" label="trading costs" />
            </label>
          </div>
        </Panel>

        <div style={{ marginTop: 16 }}>
          {touched && errors.length > 0 && <p className="err" role="alert">Fix the {errors.length} problem{errors.length > 1 ? "s" : ""} above to run.</p>}
          {error && <ErrorNote e={error} />}
          {session && (
            <button type="submit" className="btn primary" disabled={busy} data-testid="run-backtest">
              {busy ? "Queueing…" : `Run ${STRATEGY_LABEL[form.strategy]} on ${form.symbols.length} symbol${form.symbols.length === 1 ? "" : "s"}`}
            </button>
          )}
        </div>
      </form>
      {!session && <div style={{ maxWidth: 460 }}><p>Running a backtest needs a practice account.</p><TryTrading /></div>}
      <p className="hint">Runs take from a few seconds to a few minutes. <Link to="/backtests">Back to the list</Link></p>
    </div>
  );
}
