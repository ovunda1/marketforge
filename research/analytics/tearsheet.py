"""The tearsheet (M9): one self-contained HTML page per run, rendered from ``metrics.json``
alone (``render``), gross and net side by side.

No scripts, no external resources, no timestamps: the page is a pure function of the
metrics, so a reproduced run reproduces it byte for byte. Charts are inline SVG with
native ``<title>`` tooltips; every chart has a table with the same numbers. Colors are
the reference data-viz palette's first two categorical slots (gross blue, net orange),
with selected light and dark steps.

``check_portfolio_level`` is the guard on what may be published: no key named after a
symbol, and no long array except the allowed portfolio series.
"""

from __future__ import annotations

import html
import json
import math
from typing import Any

from research.analytics.report import SERIES

LONG = 60  # longer lists are daily series: only the portfolio ones are allowed


def check_portfolio_level(metrics: dict[str, Any], symbols: list[str] | tuple[str, ...]) -> None:
    """Raise ``ValueError`` if ``metrics`` could hold per-symbol series."""
    names = set(symbols)
    allowed = {("series", s) for s in SERIES}

    def walk(v: Any, path: tuple[str, ...]) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in names:
                    raise ValueError(f"not portfolio-level: key {'.'.join((*path, k))} names a symbol")
                walk(x, (*path, k))
        elif isinstance(v, list):
            if len(v) > LONG:
                if path not in allowed:
                    raise ValueError(f"not portfolio-level: {'.'.join(path)} is a {len(v)}-long series")
                if any(isinstance(x, (list, dict)) for x in v):
                    raise ValueError(f"not portfolio-level: {'.'.join(path)} holds nested values")
            for i, x in enumerate(v):
                walk(x, (*path, str(i)))

    walk(metrics, ())


# --- Formatting -----------------------------------------------------------------------------------

def _e(x: Any) -> str:
    return html.escape(str(x), quote=True)


def pct(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "n/a"
    text = f"{x * 100:.{digits}f}%"
    return text[1:] if text.startswith("-") and float(text[:-1]) == 0 else text  # no "-0%"


def fnum(x: float | None, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def dollars(units: int | float | None, unit: float = 1e-6) -> str:
    if units is None:
        return "n/a"
    v = units * unit
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def bars(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.0f} bars"


# --- Charts ---------------------------------------------------------------------------------------

W, H = 720, 240
PAD_L, PAD_R, PAD_T, PAD_B = 56, 72, 12, 28


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1e-9
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    start = math.floor(lo / step) * step
    out = []
    v = start
    while v <= hi + step * 1e-9:
        out.append(round(v, 12))
        v += step
    return out


def line_chart(title: str, dates: list[str], series: list[tuple[str, str, list[float]]], fmt: Any,
               zero: bool = True) -> str:
    """Lines over time (2px, end labels), hairline grid, hover buckets with <title>."""
    n = len(dates)
    if n < 2:
        return ""
    step = max(1, n // 400)
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    values = [v for _, _, s in series for v in s]
    lo, hi = min(values), max(values)
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    ticks = _ticks(lo, hi)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(i: int) -> float:
        return PAD_L + pw * i / (n - 1)

    def y(v: float) -> float:
        return PAD_T + ph * (hi - v) / (hi - lo)

    parts = [f'<figure class="chart"><figcaption>{_e(title)}</figcaption>',
             f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_e(title)}">']
    for t in ticks:
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(f'<text class="tick" x="{PAD_L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{_e(fmt(t))}</text>')
    # A year is labeled at its first bar when it starts inside the range, and labels
    # never sit closer than 40px (about one label's width).
    starts = [i for i in range(1, n) if dates[i][:4] != dates[i - 1][:4]]
    every = max(1, math.ceil(len(starts) / 8))
    last_x = -1e9
    for k, i in enumerate(starts):
        if k % every == 0 and x(i) - last_x >= 40:
            last_x = x(i)
            parts.append(f'<text class="tick" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{dates[i][:4]}</text>')
    parts.append(f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(0.0) if lo <= 0 <= hi else H - PAD_B:.1f}" '
                 f'y2="{y(0.0) if lo <= 0 <= hi else H - PAD_B:.1f}"/>')
    label_y = [y(s[-1]) + 4 for _, _, s in series]
    if len(label_y) == 2 and abs(label_y[0] - label_y[1]) < 13:
        mid = (label_y[0] + label_y[1]) / 2
        top = 0 if series[0][2][-1] >= series[1][2][-1] else 1
        label_y[top], label_y[1 - top] = mid - 6.5, mid + 6.5
    for (label, css, s), ly in zip(series, label_y):
        pts = " ".join(f"{x(i):.1f},{y(s[i]):.1f}" for i in idx)
        parts.append(f'<polyline class="line {css}" points="{pts}"/>')
        parts.append(f'<text class="end" x="{W - PAD_R + 6}" y="{ly:.1f}">{_e(label)} {_e(fmt(s[-1]))}</text>')
    # Hover: one transparent column per ~2% of the width, titled with its last bar's values.
    buckets = 50
    for b in range(buckets):
        i0, i1 = b * (n - 1) // buckets, (b + 1) * (n - 1) // buckets
        text = dates[i1] + "".join(f"\n{label}: {fmt(s[i1])}" for label, _, s in series)
        parts.append(f'<rect class="hit" x="{x(i0):.1f}" y="{PAD_T}" width="{x(i1) - x(i0):.1f}" height="{ph}">'
                     f'<title>{_e(text)}</title></rect>')
    parts.append("</svg>")
    if len(series) > 1:
        parts.append('<div class="legend">' + "".join(
            f'<span><i class="key {css}"></i>{_e(label)}</span>' for label, css, _ in series) + "</div>")
    parts.append("</figure>")
    return "".join(parts)


def hbar_chart(title: str, rows: list[tuple[str, float, str]]) -> str:
    """Horizontal bars from a zero baseline, value at the tip (label, value, text)."""
    if not rows:
        return ""
    bh, gap, label_w = 20, 8, 140
    h = PAD_T + len(rows) * (bh + gap) + 8
    vmax = max(abs(v) for _, v, _ in rows) or 1.0
    pw = W - label_w - 120
    parts = [f'<figure class="chart"><figcaption>{_e(title)}</figcaption>',
             f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{_e(title)}">',
             f'<line class="axis" x1="{label_w}" x2="{label_w}" y1="{PAD_T - 4}" y2="{h - 6}"/>']
    for k, (label, v, text) in enumerate(rows):
        y0 = PAD_T + k * (bh + gap)
        w = max(pw * abs(v) / vmax, 0.0)
        r = min(4.0, w / 2)
        x0 = label_w + 1
        path = (f"M{x0},{y0} h{max(w - r, 0):.1f} a{r:.1f},{r:.1f} 0 0 1 {r:.1f},{r:.1f} v{bh - 2 * r:.1f} "
                f"a{r:.1f},{r:.1f} 0 0 1 {-r:.1f},{r:.1f} h{-max(w - r, 0):.1f} z")
        parts.append(f'<path class="bar s1" d="{path}"><title>{_e(label)}: {_e(text)}</title></path>')
        parts.append(f'<text class="label" x="{label_w - 8}" y="{y0 + bh - 6}" text-anchor="end">{_e(label)}</text>')
        parts.append(f'<text class="value" x="{x0 + w + 6:.1f}" y="{y0 + bh - 6}">{_e(text)}</text>')
    parts.append("</svg></figure>")
    return "".join(parts)


def capacity_chart(points: list[dict[str, Any]]) -> str:
    if len(points) < 2:
        return ""
    n = len(points)
    series = [("Gross", "s1", [p["gross_sharpe"] or 0.0 for p in points]),
              ("Net", "s2", [p["net_sharpe"] or 0.0 for p in points])]
    values = [v for _, _, s in series for v in s]
    ticks = _ticks(min(min(values), 0.0), max(max(values), 0.0))
    lo, hi = ticks[0], ticks[-1]
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(i: int) -> float:
        return PAD_L + pw * i / (n - 1)

    def y(v: float) -> float:
        return PAD_T + ph * (hi - v) / (hi - lo)

    title = "Capacity: Sharpe as capital scales"
    parts = [f'<figure class="chart"><figcaption>{title}</figcaption>',
             f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{title}">']
    for t in ticks:
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(f'<text class="tick" x="{PAD_L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t:.2f}</text>')
    parts.append(f'<line class="axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(0.0):.1f}" y2="{y(0.0):.1f}"/>')
    for i, p in enumerate(points):
        parts.append(f'<text class="tick" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">'
                     f'{_e(dollars(p["capital"]))}</text>')
    for label, css, s in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s))
        parts.append(f'<polyline class="line {css}" points="{pts}"/>')
        for i, v in enumerate(s):
            parts.append(f'<circle class="dot {css}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="4">'
                         f'<title>{_e(dollars(points[i]["capital"]))}: {label} Sharpe {v:.2f}</title></circle>')
        parts.append(f'<text class="end" x="{W - PAD_R + 8}" y="{y(s[-1]) + 4:.1f}">{label} {s[-1]:.2f}</text>')
    parts.append('</svg><div class="legend"><span><i class="key s1"></i>Gross</span>'
                 '<span><i class="key s2"></i>Net</span></div></figure>')
    return "".join(parts)


# --- Page ----------------------------------------------------------------------------------------------

CSS = """
.ts{color-scheme:light;--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,0.10);--s1:#2a78d6;--s2:#eb6834}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .ts{color-scheme:dark;--surface:#1a1a19;
--plane:#0d0d0d;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,0.10);--s1:#3987e5;--s2:#d95926}}
:root[data-theme="dark"] .ts{color-scheme:dark;--surface:#1a1a19;--plane:#0d0d0d;--ink:#f0efec;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,0.10);--s1:#3987e5;--s2:#d95926}
body{margin:0;background:var(--plane)}
.ts{background:var(--plane);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
max-width:960px;margin:0 auto;padding:24px 16px 48px}
.ts h1{font-size:22px;margin:0 0 4px}.ts h2{font-size:16px;margin:4px 0 8px}
.ts .sub{color:var(--ink2);margin:0 0 16px}.ts .note{color:var(--ink2);font-size:13px}
.ts section{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px 16px;
margin:12px 0;overflow-x:auto}
.ts table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
.ts th,.ts td{text-align:right;padding:4px 8px;border-bottom:1px solid var(--grid);white-space:nowrap}
.ts th:first-child,.ts td:first-child{text-align:left}.ts th{color:var(--ink2);font-weight:600}
.ts .chart{margin:8px 0}.ts figcaption{color:var(--ink2);font-weight:600;margin-bottom:4px}
.ts svg{width:100%;height:auto;display:block}
.ts .grid{stroke:var(--grid);stroke-width:1}.ts .axis{stroke:var(--axis);stroke-width:1}
.ts .tick,.ts .label{fill:var(--muted);font-size:11px}.ts .end,.ts .value{fill:var(--ink2);font-size:11px}
.ts .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.ts .line.s1{stroke:var(--s1)}.ts .line.s2{stroke:var(--s2)}
.ts .dot{stroke:var(--surface);stroke-width:2}.ts .dot.s1{fill:var(--s1)}.ts .dot.s2{fill:var(--s2)}
.ts .bar.s1{fill:var(--s1)}.ts .hit{fill:transparent}.ts .hit:hover{fill:var(--grid);fill-opacity:.35}
.ts .legend{display:flex;gap:16px;color:var(--ink2);font-size:12px;margin-top:4px}
.ts .key{display:inline-block;width:14px;height:2px;margin-right:6px;vertical-align:middle}
.ts .key.s1{background:var(--s1)}.ts .key.s2{background:var(--s2)}
.ts pre{background:var(--plane);padding:8px;border-radius:6px;overflow-x:auto;font-size:12px}
"""


def _table(head: list[str], rows: list[list[str]]) -> str:
    return ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
            + "</tbody></table>")


PERF_ROWS: list[tuple[str, str, Any]] = [
    ("Total return", "total_return", pct),
    ("Annual return", "annual_return", pct),
    ("Annual excess return", "annual_excess_return", pct),
    ("Volatility", "volatility", pct),
    ("Sharpe", "sharpe", fnum),
    ("Sortino", "sortino", fnum),
    ("Calmar", "calmar", fnum),
    ("Max drawdown", "max_drawdown", pct),
    ("Max drawdown duration", "max_drawdown_duration", bars),
    ("Beta to benchmark", "beta", fnum),
    ("Alpha (annual)", "alpha_annual", pct),
    ("Hit rate (episodes)", "hit_rate", lambda v: pct(v, 1)),
    ("Average win (episode)", "avg_win", dollars),
    ("Average loss (episode)", "avg_loss", dollars),
    ("Profit factor", "profit_factor", fnum),
    ("Daily hit rate", "daily_hit_rate", lambda v: pct(v, 1)),
]

UNCERTAINTY_ROWS: list[tuple[str, str, Any]] = [
    ("Sharpe standard error (iid)", "sharpe_se_iid", fnum),
    ("Sharpe standard error (skew, kurtosis)", "sharpe_se_mertens", fnum),
    ("Probabilistic Sharpe, P(SR > 0)", "psr", lambda v: pct(v, 1)),
    ("Deflated Sharpe, P(SR > best of N null trials)", "dsr", lambda v: pct(v, 1)),
    ("Deflated Sharpe hurdle (annual SR)", "dsr_sr0_annual", fnum),
]


def _gn(perf: dict[str, Any], rows: list[tuple[str, str, Any]]) -> str:
    out = []
    for label, key, f in rows:
        v = perf.get(key, {})
        out.append([label, f(v.get("gross")), f(v.get("net"))])
    return ("<table><thead><tr><th>Metric</th><th>Gross</th><th>Net</th></tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in out)
            + "</tbody></table>")


def _model(model: dict[str, Any]) -> str:
    shown = json.loads(json.dumps(model))
    sec = shown.get("sec_fee", {})
    if isinstance(sec.get("schedule"), list):
        # Rates and dates only: the sources stay in metrics.json (no links in the page).
        sec["schedule"] = [f'{r["from"]}: ${r["rate_per_million"]} per million' for r in sec["schedule"]]
    return "<pre>" + _e(json.dumps(shown, indent=2, sort_keys=True)) + "</pre>"


def render(metrics: dict[str, Any]) -> str:
    if metrics["kind"] == "event":
        body = _render_event(metrics)
        title = f"{metrics['strategy']['name']} (event)"
    else:
        body = _render_bars(metrics)
        title = f"{metrics['strategy']['name']} ({metrics['kind']})"
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_e(title)} tearsheet</title><style>{CSS}</style></head>"
            f"<body><main class=\"ts\">{body}</main></body></html>\n")


def _data_line(metrics: dict[str, Any]) -> str:
    return "; ".join(f"{d['name']}: {d.get('path') or 'generated'} ({d['sha256'][:12]})" for d in metrics["data"])


def _render_bars(mt: dict[str, Any]) -> str:
    perf, costs, sf, tr = mt["performance"], mt["costs"], mt["implementation_shortfall"], mt["trading"]
    s = mt["series"]
    period = mt["period"]
    strat = mt["strategy"]
    out = [f"<h1>{_e(strat['name'])}: gross and net</h1>",
           f"<p class=\"sub\">{_e(mt['kind'])}, out-of-sample {_e(period['start'])} to {_e(period['end'])} "
           f"({period['bars']} bars), capital {dollars(mt['capital'])}. Data: {_e(_data_line(mt))}.</p>"]
    pnl = mt["pnl"]
    out.append("<section><h2>Performance</h2>" + _gn(perf, PERF_ROWS)
               + f"<p class=\"note\">P&amp;L {dollars(pnl['gross'])} gross, {dollars(pnl['net'])} net, after "
               f"{dollars(costs['total'])} of costs. Episodes: {tr['episodes_closed']} closed.</p></section>")
    out.append("<section><h2>Equity and drawdown</h2>"
               + line_chart("Cumulative return on capital", s["dates"],
                            [("Gross", "s1", s["gross_cum_return"]), ("Net", "s2", s["net_cum_return"])],
                            lambda v: pct(v, 0))
               + line_chart("Net drawdown from peak", s["dates"], [("Net", "s2", s["net_drawdown"])],
                            lambda v: pct(v, 0))
               + "</section>")
    comp = sorted(costs["by_component"].items(), key=lambda kv: (-kv[1], kv[0]))
    drag = mt["cost_drag"]
    out.append("<section><h2>Costs</h2>"
               + hbar_chart("Costs by component", [(k, float(v), dollars(v)) for k, v in comp if v])
               + _table(["Component", "Total", "Share"],
                        [[k, dollars(v), pct(v / costs["total"], 1) if costs["total"] else "n/a"] for k, v in comp])
               + f"<p class=\"note\">Annual cost {pct(drag['annual_cost'])} of capital against an annual gross excess "
               f"return of {pct(drag['gross_excess_annual'])}: costs could be "
               f"{fnum(drag['break_even_multiple'])}x before the net excess return reaches zero. "
               f"{fnum(costs['bps_of_traded_notional'])} bp of traded notional.</p>"
               + _table(["Year", *[k for k, _ in comp], "Total"],
                        [[y, *[dollars(row[k]) for k, _ in comp], dollars(sum(row.values()))]
                         for y, row in sorted(costs["by_year"].items())])
               + "</section>")
    out.append("<section><h2>Implementation shortfall</h2>"
               + _table(["Part", "Amount"], [["Delay (decision close to fill)", dollars(sf["delay"])],
                                              *[[k, dollars(v)] for k, v in sorted(sf["costs"].items())],
                                              ["Total", dollars(sf["total"])],
                                              ["bp of traded notional", fnum(sf["bps_of_traded_notional"])]])
               + f"<p class=\"note\">{_e(sf['decision_price'])}.</p></section>")
    ex = tr["exposure"]
    out.append("<section><h2>Trading</h2>" + _table(["Measure", "Value"], [
        ["Trades", f"{tr['trades']:,} ({tr['liquidation_trades']:,} window-end liquidations)"],
        ["Traded notional", dollars(tr["traded_notional"])],
        ["Turnover (buys + sells over capital, a year)", fnum(tr["turnover_annual"])],
        ["Holding period (FIFO, share-weighted)", bars(tr["holding_period_bars"])],
        ["Median episode", bars(tr["median_episode_bars"])],
        ["Average gross exposure", pct(ex["gross"], 1)],
        ["Average net exposure", pct(ex["net"], 1)],
        ["Time in market", pct(ex["time_in_market"], 1)],
        ["Max participation (notional over ADV)", pct(tr["max_participation"], 2)],
        ["Trades charged the fallback (short history)", f"{tr['fallback_trades']:,}"],
    ]) + "</section>")
    t = mt["trials"]
    out.append("<section><h2>Uncertainty and trials</h2>" + _gn(perf, UNCERTAINTY_ROWS)
               + _table(["Trials", "Count"], [["Parameter sets in the grid", str(t["grid_size"])],
                                              ["Walk-forward folds", str(t["folds"])],
                                              ["Backtests run (selection and test)", str(t["backtests"])],
                                              ["N for the deflated Sharpe", str(t["dsr_trials"])],
                                              ["Capacity points (re-runs at other capital)",
                                               str(t["capacity_points"])]])
               + "<p class=\"note\">Every reported number is out of sample; parameters were chosen on the "
                 "training window before each test window.</p></section>")
    if mt["capacity"]:
        out.append("<section><h2>Capacity</h2>" + capacity_chart(mt["capacity"])
                   + _table(["Capital", "Gross Sharpe", "Net Sharpe", "Annual cost", "Max participation",
                             "Median participation"],
                            [[dollars(p["capital"]), fnum(p["gross_sharpe"]), fnum(p["net_sharpe"]),
                              pct(p["annual_cost"]), pct(p["max_participation"]), pct(p["median_participation"], 3)]
                             for p in mt["capacity"]])
                   + "<p class=\"note\">Each point re-runs the whole walk-forward at that capital: selection is "
                     "net of that size's costs. Fills are never capped; participation is reported.</p></section>")
    months = mt["monthly_net_returns"]
    out.append("<section><h2>Monthly net returns</h2>" + _table(
        ["Year", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        [[y, *[pct(v, 1) if v is not None else "" for v in months[y]]] for y in sorted(months)]) + "</section>")
    conv = mt["conventions"]
    out.append("<section><h2>Conventions and assumptions</h2>" + _table(["Item", "Value"], [
        [k, json.dumps(v, sort_keys=True) if isinstance(v, dict) else str(v)] for k, v in sorted(conv.items())])
               + "<h2>Cost model</h2>" + _model(costs["model"])
               + "<h2>Strategy and execution</h2><pre>"
               + _e(json.dumps({"strategy": strat, "execution": mt["execution"]}, indent=2, sort_keys=True))
               + "</pre></section>")
    return "".join(out)


def _render_event(mt: dict[str, Any]) -> str:
    pnl, costs, sf, tr = mt["pnl"], mt["costs"], mt["implementation_shortfall"], mt["trading"]
    out = [f"<h1>{_e(mt['strategy']['name'])}: event-driven, gross and net</h1>",
           f"<p class=\"sub\">Data: {_e(_data_line(mt))}. Money in units of {_e(mt['conventions']['money_unit'])}.</p>",
           "<section><h2>P&amp;L</h2><table><thead><tr><th>Item</th><th>Gross</th><th>Net</th></tr></thead><tbody>"
           f"<tr><td>P&amp;L</td><td>{_e(pnl['gross'])}</td><td>{_e(pnl['net'])}</td></tr></tbody></table>"
           f"<p class=\"note\">{_e(mt['conventions']['gross'])}. Costs: {_e(mt['conventions']['costs'])}.</p>"
           "</section>",
           "<section><h2>Costs</h2>" + _table(["Component", "Total"], [[k, str(v)] for k, v in
                                                                   sorted(costs["by_component"].items())]
                                              + [["Total", str(costs["total"])]]) + _model(costs["model"])
           + "</section>",
           "<section><h2>Implementation shortfall (measured, inside gross)</h2>" + _table(["Part", "Amount"], [
               ["Delay (decision mid to arrival mid)", fnum(sf["delay"], 1)],
               ["Spread paid (fill against arrival mid)", fnum(sf["spread_paid"], 1)],
               ["Fills without a two-sided quote", str(sf["fills_without_quotes"])]])
           + f"<p class=\"note\">{_e(sf['note'])}.</p></section>",
           "<section><h2>Trading</h2>" + _table(["Measure", "Value"], [
               ["Orders", str(tr["orders"])], ["Fills", str(tr["fills"])],
               ["Maker share of fills", pct(tr["maker_share"], 1)], ["Traded quantity", str(tr["traded_qty"])],
               ["Final position", str(mt["performance"]["final_position"])]]) + "</section>"]
    return "".join(out)
