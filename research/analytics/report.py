"""A run's metrics (M9): what ``metrics.json`` holds and the tearsheet renders.

Everything is portfolio-level (``research.analytics.tearsheet.check_portfolio_level``):
per-symbol arrays are used to compute episode statistics and the holding period, and
never leave this module. Floats are rounded to 12 significant digits (``num``) so equal
runs give equal bytes on every platform.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from research.analytics import metrics as m
from research.backtest.bars import BarPanel
from research.backtest.costs import CostModel
from research.backtest.event import EventResult
from research.backtest.rates import RiskFree
from research.backtest.vectorized import FINANCING_COMPONENTS, BarResult

SERIES = ("dates", "gross_cum_return", "net_cum_return", "net_drawdown")


def num(x: Any) -> Any:
    """JSON-safe, platform-stable numbers: 12 significant digits, None for nan/inf."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, (int, np.integer)):
        return int(x)
    x = float(x)
    if not math.isfinite(x):
        return None
    return float(f"{x:.12g}")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return num(value)


def _concat(runs: Sequence[BarResult], attr: str) -> np.ndarray:
    return np.concatenate([getattr(r, attr) for r in runs])


def benchmark_returns(panel: BarPanel, symbol: str, runs: Sequence[BarResult]) -> np.ndarray:
    j = panel.symbols.index(symbol)
    tr = panel.total_return_index()[:, j]
    out = []
    for r in runs:
        idx = np.arange(r.start, r.start + len(r.dates))
        prev = np.where(idx > 0, idx - 1, 0)
        ret = tr[idx] / tr[prev] - 1
        ret[idx == 0] = np.nan
        out.append(ret)
    return np.concatenate(out)


def _performance(gross: np.ndarray, net: np.ndarray, rf: np.ndarray, capital: int, periods: int,
                 bench: np.ndarray | None, n_trials: int, episodes: dict[str, list[int]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for label, pnl in (("gross", gross), ("net", net)):
        r = pnl / capital
        equity = np.concatenate([[capital], capital + np.cumsum(pnl)])
        dd = m.drawdown(equity)
        se = m.sharpe_se(r, rf, periods)
        b = m.beta(r, bench, rf, periods) if bench is not None else {"beta": None, "alpha_annual": None}
        w = m.win_stats(episodes[label])
        vals = {
            "total_return": float(np.sum(pnl)) / capital,
            "annual_return": m.annual_return(r, periods),
            "annual_excess_return": m.annual_return(r - rf, periods),
            "volatility": m.volatility(r, periods),
            "sharpe": m.sharpe(r, rf, periods),
            "sortino": m.sortino(r, rf, periods),
            "calmar": m.calmar(r, equity, periods),
            "max_drawdown": dd.max_drawdown,
            "max_drawdown_duration": dd.duration,
            "beta": b["beta"],
            "alpha_annual": b["alpha_annual"],
            "hit_rate": w["hit_rate"],
            "avg_win": w["avg_win"],
            "avg_loss": w["avg_loss"],
            "profit_factor": w["profit_factor"],
            "daily_hit_rate": float(np.mean(pnl > 0)) if len(pnl) else None,
            "sharpe_se_iid": se["iid"],
            "sharpe_se_mertens": se["mertens"],
            "psr": m.psr(r, 0.0, rf),
            "dsr": m.dsr(r, n_trials, rf, periods)["probability"],
        }
        for k, v in vals.items():
            out.setdefault(k, {})[label] = v
    out["dsr_sr0_annual"] = {"gross": m.dsr(gross / capital, n_trials, rf, periods)["sr0_annual"],
                             "net": m.dsr(net / capital, n_trials, rf, periods)["sr0_annual"]}
    return out


def bar_metrics(*, kind: str, runs: Sequence[BarResult], panel: BarPanel, capital: int, periods: int,
                cost_model: CostModel, risk_free: RiskFree, benchmark: str | None, trials: dict[str, int],
                capacity: list[dict[str, Any]], strategy: dict[str, Any], data: list[dict[str, Any]],
                execution: dict[str, Any]) -> dict[str, Any]:
    dates = _concat(runs, "dates").astype("datetime64[D]")
    gross = _concat(runs, "pnl").astype(np.int64)
    net = _concat(runs, "net_pnl_per_bar").astype(np.int64)
    rf = _concat(runs, "rf_returns")
    by_component = {k: np.concatenate([r.costs_by_component[k] for r in runs]) for k in runs[0].costs_by_component}
    total_costs = int(sum(int(v.sum()) for v in by_component.values()))
    bench = benchmark_returns(panel, benchmark, runs) if benchmark else None

    # Episodes and holding period, per run (each window starts flat).
    ep_gross: list[int] = []
    ep_net: list[int] = []
    still_open, durations = 0, []
    hp_weighted, hp_shares = 0.0, 0.0
    trades = [t for r in runs for t in r.trades]
    for r in runs:
        pos, liquidated = pos_before_liquidation(r)
        eps, open_ = m.episodes(r.held, pos, r.old_leg, r.new_leg, r.symbol_costs, close_at_end=liquidated)
        still_open += open_
        ep_gross += [e.gross for e in eps]
        ep_net += [e.net for e in eps]
        durations += [e.duration for e in eps]
        rel = [(t.bar - r.start, t.symbol, t.qty) for t in r.trades]
        hp = m.holding_period(rel, r.split_ratio)
        if hp is not None:
            shares = sum(abs(q) for _, _, q in rel) / 2
            hp_weighted += hp * shares
            hp_shares += shares

    notional = sum(t.notional for t in trades)
    trade_costs = {k: int(sum(int(v.sum()) for v in (r.trade_costs[k] for r in runs))) for k in runs[0].trade_costs}
    delay = sum(t.qty * (t.price - t.decision_price) for t in trades if t.decision_price is not None)
    participation = np.concatenate([r.trade_participation for r in runs])
    fallback = int(sum(int(r.trade_fallback.sum()) for r in runs))
    years: dict[str, dict[str, int]] = {}
    for i, d in enumerate(dates):
        y = str(d)[:4]
        row = years.setdefault(y, {k: 0 for k in by_component})
        for k, v in by_component.items():
            row[k] += int(v[i])
    cum_g = np.cumsum(gross) / capital
    cum_n = np.cumsum(net) / capital
    eq_n = np.concatenate([[capital], capital + np.cumsum(net)])
    peak = np.maximum.accumulate(eq_n)
    dd_series = ((eq_n - peak) / peak)[1:]
    monthly: dict[str, list[float | None]] = {}
    months = dates.astype("datetime64[M]")
    for mo in np.unique(months):
        y, k = str(mo)[:4], int(str(mo)[5:7]) - 1
        monthly.setdefault(y, [None] * 12)[k] = float(net[months == mo].sum()) / capital

    excess_gross = gross / capital - rf
    out = {
        "kind": kind,
        "strategy": strategy,
        "data": data,
        "period": {"start": str(dates[0]), "end": str(dates[-1]), "bars": int(len(dates))},
        "capital": capital,
        "execution": execution,
        "conventions": {
            "periods_per_year": periods,
            "returns": "P&L over the fixed starting capital (no compounding)",
            "annualization": "arithmetic: mean per-period return x periods; volatility x sqrt(periods)",
            "std": "sample (ddof=1)",
            "sharpe": "mean excess return over the sample std of excess returns, x sqrt(periods)",
            "sortino": "mean excess return over sqrt(mean(min(excess, 0)^2)), all periods",
            "sharpe_se": "iid: sqrt((1 + SR^2/2)/T); mertens: with sample skew and kurtosis; no autocorrelation "
                         "correction",
            "dsr": "PSR against the expected maximum Sharpe of dsr_trials zero-skill trials, null variance 1/(T-1)",
            "episodes": "a run of bars a symbol is held with one sign; trade costs go to the episode they open or "
                        "close (a flip splits them by size); financing is not allocated",
            "risk_free": risk_free.describe(),
            "benchmark": f"{benchmark} (total return)" if benchmark else None,
        },
        "trials": trials,
        "pnl": {"gross": int(gross.sum()), "net": int(net.sum())},
        "costs": {
            "total": total_costs,
            "by_component": {k: int(v.sum()) for k, v in by_component.items()},
            "by_year": years,
            "model": cost_model.describe(),
            "bps_of_traded_notional": (total_costs / notional * 10_000) if notional else None,
        },
        "cost_drag": m.cost_drag(excess_gross, np.add.reduce(list(by_component.values())), capital, periods),
        "implementation_shortfall": {
            "delay": int(delay),
            "costs": trade_costs,
            "total": int(delay + sum(trade_costs.values())),
            "bps_of_traded_notional": ((delay + sum(trade_costs.values())) / notional * 10_000) if notional else None,
            "decision_price": "the signal bar's close (split-adjusted); delay is the move from it to the fill",
        },
        "performance": _performance(gross, net, rf, capital, periods, bench, trials["dsr_trials"],
                                    {"gross": ep_gross, "net": ep_net}),
        "trading": {
            "trades": len(trades),
            "liquidation_trades": sum(1 for t in trades if t.kind == "liquidation"),
            "traded_notional": int(notional),
            "turnover_annual": m.turnover(notional, capital, len(dates), periods),
            "holding_period_bars": hp_weighted / hp_shares if hp_shares else None,
            "episodes_closed": len(ep_net),
            "episodes_open_at_end": still_open,
            "median_episode_bars": float(np.median(durations)) if durations else None,
            "exposure": m.exposure(_concat(runs, "gross_exposure"), _concat(runs, "net_exposure"), capital),
            "max_participation": float(np.nanmax(participation)) if np.isfinite(participation).any() else None,
            "fallback_trades": fallback,
        },
        "capacity": capacity,
        "monthly_net_returns": monthly,
        "series": {
            "dates": [str(d) for d in dates],
            "gross_cum_return": cum_g.tolist(),
            "net_cum_return": cum_n.tolist(),
            "net_drawdown": dd_series.tolist(),
        },
    }
    return _clean(out)


def pos_before_liquidation(r: BarResult) -> tuple[np.ndarray, bool]:
    """Positions after each bar's rebalance trades, and whether the window ended in a
    liquidation: its trades are at the close (no P&L), so the episodes they end are
    closed on that bar rather than counted as open."""
    liquidated = any(t.kind == "liquidation" for t in r.trades)
    pos = r.positions.copy()
    if liquidated:
        last_bar = r.start + len(r.dates) - 1
        last = r.held[-1].copy()
        for t in r.trades:
            if t.bar == last_bar and t.kind == "rebalance":
                last[t.symbol] += t.qty
        pos[-1] = last
    return pos, liquidated


def capacity_point(capital: int, runs: Sequence[BarResult], periods: int) -> dict[str, Any]:
    gross = _concat(runs, "pnl") / capital
    net = _concat(runs, "net_pnl_per_bar") / capital
    rf = _concat(runs, "rf_returns")
    part = np.concatenate([r.trade_participation for r in runs])
    costs = sum(int(sum(int(v.sum()) for v in r.costs_by_component.values())) for r in runs)
    return _clean({
        "capital": capital,
        "gross_sharpe": m.sharpe(gross, rf, periods),
        "net_sharpe": m.sharpe(net, rf, periods),
        "annual_cost": costs / capital * periods / len(gross),
        "max_participation": float(np.nanmax(part)) if np.isfinite(part).any() else None,
        "median_participation": float(np.nanmedian(part)) if np.isfinite(part).any() else None,
    })


def event_metrics(*, result: EventResult, strategy: dict[str, Any], data: list[dict[str, Any]],
                  cost_model: CostModel, tick_size: str, orders: int) -> dict[str, Any]:
    fills = result.fills
    gross = result.pnl
    total = sum(result.costs.values())
    makers = sum(1 for f in fills if f.liquidity.name == "MAKER")
    sf = result.shortfall
    out = {
        "kind": "event",
        "strategy": strategy,
        "data": data,
        "conventions": {"money_unit": f"{tick_size} dollars (the instrument's tick) per unit of price x qty",
                        "gross": "fills at the engine's prices, the open position marked at the exit side: the "
                                 "spread, queue and latency are already in it",
                        "costs": "fees only: nothing else may be charged on top of a book fill"},
        "pnl": {"gross": gross, "net": result.net_pnl},
        "costs": {"total": total, "by_component": dict(result.costs), "model": cost_model.describe()},
        "implementation_shortfall": {
            "delay": float(sf["delay"]), "spread_paid": float(sf["spread_paid"]),
            "fills_without_quotes": sf["fills_without_quotes"],
            "note": "measured, inside gross: delay is the mid moving from the decision to the order's arrival; "
                    "spread_paid is each fill against the arrival mid",
        },
        "performance": {"final_position": result.position, "mark": result.mark},
        "trading": {"fills": len(fills), "orders": orders, "maker_fills": makers,
                    "maker_share": makers / len(fills) if fills else None,
                    "traded_qty": sum(f.qty for f in fills),
                    "traded_notional": sum(f.qty * f.price for f in fills)},
    }
    return _clean(out)
