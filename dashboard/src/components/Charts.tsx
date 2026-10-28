// Backtest charts (M11, M11b), hand-written SVG: equity curve (gross and net), drawdown, and
// the cost breakdown. Thin marks, recessive grid, one axis per chart, a legend whenever there
// are two series plus direct end labels, a crosshair tooltip on hover or keyboard, and a
// data table for each chart. Net is solid black, gross dashed grey: line style as well as
// color tells them apart (checked with the dataviz validator on white).
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { charts as C } from "../copy";
import { microDollars, pct } from "../lib/format";

const keyStyle = (key: Series["key"]) => ({ borderTopColor: `var(--series-${key})`,
                                            borderTopStyle: key === "gross" ? "dashed" as const : "solid" as const });

const M = { top: 12, right: 64, bottom: 28, left: 56 };

/** The element's width in CSS pixels, so the SVG draws 1:1 and its text keeps its size on a phone. */
function useWidth<T extends HTMLElement>(fallback = 760) {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => { if (e && e.contentRect.width > 0) setW(Math.round(e.contentRect.width)); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceTicks(lo: number, hi: number, n = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/** Every k-th point (and the last), so a 3,500-day series draws as a few hundred segments. */
function thin(n: number, max = 700): number[] {
  const k = Math.max(1, Math.ceil(n / max));
  const idx = [];
  for (let i = 0; i < n; i += k) idx.push(i);
  if (idx[idx.length - 1] !== n - 1) idx.push(n - 1);
  return idx;
}

export interface Series { key: "net" | "gross"; label: string; values: number[] }

export function LineChart({ title, dates, series, area = false, format = (v: number) => pct(v, 1) }: {
  title: string; dates: string[]; series: Series[]; area?: boolean; format?: (v: number) => string;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const [ref, W] = useWidth<HTMLElement>();
  const H = Math.round(Math.min(380, Math.max(220, W * 0.32)));
  const n = dates.length;
  const { lo, hi, ticks, xYears } = useMemo(() => {
    const all = series.flatMap((s) => s.values).filter(Number.isFinite);
    const lo0 = Math.min(0, ...all), hi0 = Math.max(0, ...all);
    const pad = (hi0 - lo0) * 0.05 || 0.01;
    const t = niceTicks(lo0 - pad, hi0 + pad);
    const years: { i: number; y: string }[] = [];
    // A label at each year's first day; the first, partial year only if it starts in January.
    dates.forEach((d, i) => {
      if (i === 0 ? d.slice(5, 7) === "01" : d.slice(0, 4) !== dates[i - 1]!.slice(0, 4)) years.push({ i, y: d.slice(0, 4) });
    });
    const every = Math.ceil(years.length / Math.max(3, Math.floor(W / 110)));
    return { lo: Math.min(lo0 - pad, t[0]!), hi: Math.max(hi0 + pad, t[t.length - 1]!), ticks: t,
             xYears: years.filter((_, k) => k % every === 0) };
  }, [dates, series, W]);
  if (n < 2) return <p className="muted">{C.tooShort}</p>;
  const x = (i: number) => M.left + (i / (n - 1)) * (W - M.left - M.right);
  const y = (v: number) => M.top + (1 - (v - lo) / (hi - lo)) * (H - M.top - M.bottom);
  const idx = thin(n);
  const path = (vals: number[]) => idx.map((i, k) => `${k ? "L" : "M"}${x(i).toFixed(1)},${y(vals[i]!).toFixed(1)}`).join("");

  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - r.left) / r.width;
    setHover(Math.max(0, Math.min(n - 1, Math.round(frac * (n - 1)))));
  };
  const onKey = (e: KeyboardEvent) => {
    const step = Math.max(1, Math.round(n / 50));
    if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + step));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - step));
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };
  const yearEnds = dates.map((d, i) => (i === n - 1 || d.slice(0, 4) !== dates[i + 1]!.slice(0, 4) ? i : -1)).filter((i) => i >= 0);

  return (
    <figure className="chart-wrap" ref={ref} style={{ margin: 0 }}>
      {series.length > 1 && (
        <div className="legend" aria-hidden="true">
          {series.map((s) => <span key={s.key}><span className="key" style={keyStyle(s.key)} />{s.label}</span>)}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-labelledby={`${id}-t`} tabIndex={0} onKeyDown={onKey}
           onBlur={() => setHover(null)}>
        <title id={`${id}-t`}>{C.keys(title)}</title>
        {ticks.map((t) => (
          <g key={t} className="axis">
            <line className={t === 0 ? "zero" : "grid-line"} x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
            <text x={M.left - 8} y={y(t) + 4} textAnchor="end">{format(t)}</text>
          </g>
        ))}
        {xYears.map(({ i, y: yr }) => <text key={yr} className="axis" x={x(i)} y={H - 8} textAnchor="middle">{yr}</text>)}
        {series.map((s) => (
          <g key={s.key}>
            {area && <path className={`area ${s.key}`} d={`${path(s.values)}L${x(n - 1)},${y(0)}L${x(0)},${y(0)}Z`} />}
            <path className={`series ${s.key}`} d={path(s.values)} />
            <circle className="end-dot" cx={x(n - 1)} cy={y(s.values[n - 1]!)} r={4} style={{ fill: `var(--series-${s.key})` }} />
            <text className="label" x={x(n - 1) + 8} y={y(s.values[n - 1]!) + 4}>{format(s.values[n - 1]!)}</text>
          </g>
        ))}
        {hover !== null && (
          <g aria-hidden="true">
            <line className="cross" x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} />
            {series.map((s) => <circle key={s.key} className="end-dot" cx={x(hover)} cy={y(s.values[hover]!)} r={4}
                                       style={{ fill: `var(--series-${s.key})` }} />)}
          </g>
        )}
        <rect x={M.left} y={M.top} width={W - M.left - M.right} height={H - M.top - M.bottom} fill="transparent"
              onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {hover !== null && (
        <div className="chart-tip" role="status" style={{ left: `${(x(hover) / W) * 100}%`, top: 24,
                                                           transform: x(hover) > W / 2 ? "translateX(-105%)" : "translateX(8px)" }}>
          <div className="num">{dates[hover]}</div>
          {series.map((s) => <div key={s.key}>{s.label}: <b className="num">{format(s.values[hover]!)}</b></div>)}
        </div>
      )}
      <details>
        <summary className="linkish">{C.table}</summary>
        <div className="scroll-x">
          <table className="data">
            <caption className="sr-only">{C.atYearEnd(title)}</caption>
            <thead><tr><th scope="col">{C.date}</th>{series.map((s) => <th key={s.key} scope="col" className="num">{s.label}</th>)}</tr></thead>
            <tbody>
              {yearEnds.map((i) => (
                <tr key={i}><th scope="row" className="num">{dates[i]}</th>
                  {series.map((s) => <td key={s.key} className="num">{format(s.values[i]!)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

export function CostBars({ byComponent }: { byComponent: Record<string, number | string> }) {
  const id = useId();
  const [ref, w] = useWidth<HTMLElement>();
  const rows = Object.entries(byComponent)
    .map(([k, v]) => ({ key: k, label: C.costs[k] ?? k, value: Number(v), raw: v }))
    .filter((r) => r.value !== 0)
    .sort((a, b) => b.value - a.value);
  if (!rows.length) return <p className="muted">{C.noCostsCharged}</p>;
  const max = Math.max(...rows.map((r) => Math.abs(r.value)));
  const rowH = 32, labelW = Math.min(200, Math.round(w * 0.42)), valueW = Math.min(110, Math.round(w * 0.27));
  const h = rows.length * rowH + 8;
  return (
    <figure ref={ref} style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img" aria-labelledby={`${id}-t`}>
        <title id={`${id}-t`}>{C.costsTitle}</title>
        {rows.map((r, i) => {
          const bw = Math.max(2, (Math.abs(r.value) / max) * (w - labelW - valueW));
          const y0 = 4 + i * rowH;
          return (
            <g key={r.key}>
              <text className="label" x={labelW - 10} y={y0 + 19} textAnchor="end">{r.label}</text>
              <rect className="bar" x={labelW} y={y0 + 6} width={bw} height={18}>
                <title>{`${r.label}: ${microDollars(r.raw)}`}</title>
              </rect>
              <text className="label num" x={labelW + bw + 8} y={y0 + 19}>{microDollars(r.raw)}</text>
            </g>
          );
        })}
      </svg>
      <details>
        <summary className="linkish">{C.table}</summary>
        <table className="data">
          <caption className="sr-only">{C.costsCaption}</caption>
          <thead><tr><th scope="col">{C.component}</th><th scope="col" className="num">{C.cost}</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.key}><th scope="row">{r.label}</th><td className="num">{microDollars(r.raw)}</td></tr>)}</tbody>
        </table>
      </details>
    </figure>
  );
}
