"""Performance metrics (M9): pure functions of per-period returns, equity and trades.

Conventions (recorded in every tearsheet):

- **Returns** are P&L over the fixed starting capital (positions are sized off it, no
  compounding). **Annualization**: ``periods_per_year`` periods, 252 for daily bars
  (``periods_per_year`` refuses to guess anything else). Annual return is the mean
  per-period return times the periods: arithmetic, consistent with fixed-capital P&L, and
  free of ``pow``/``log`` so results are identical across platforms.
- **Standard deviation** is the sample one (ddof = 1). Volatility is it times
  ``sqrt(periods)``.
- **Sharpe** is the mean excess return (over the per-period risk-free return ``rf``) over
  the sample standard deviation of excess returns, annualized by ``sqrt(periods)``; 0 for
  a flat series. **Sortino** divides the mean excess return by the target downside
  deviation ``sqrt(mean(min(r - rf, 0)^2))`` over all periods (N in the denominator: it
  is a target semideviation, not a sample std); undefined (None) without losses.
- **Calmar** is the annual return over the maximum drawdown of the whole period.
- **Sharpe uncertainty**: the standard error of the per-period Sharpe is
  ``sqrt((1 + SR^2/2) / T)`` for i.i.d. normal returns (Lo 2002) and
  ``sqrt((1 + SR^2/2 - g3 SR + (g4 - 3)/4 SR^2) / T)`` with the sample skewness ``g3``
  and kurtosis ``g4`` (Mertens 2002, Opdyke 2007), both annualized by ``sqrt(periods)``.
  Neither corrects for autocorrelation. The probabilistic Sharpe ratio is the
  probability that the true Sharpe exceeds ``sr0`` (Bailey and Lopez de Prado 2012);
  the deflated Sharpe raises ``sr0`` to the expected maximum of ``N`` Sharpe ratios of
  zero-skill trials, with the null variance ``1 / (T - 1)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

import numpy as np

DAILY = 252
EULER_GAMMA = 0.5772156649015329


def periods_per_year(dates: np.ndarray) -> int:
    d = np.asarray(dates).astype("datetime64[D]").astype(np.int64)
    if len(d) < 2:
        return DAILY
    gap = float(np.median(np.diff(d)))
    if gap <= 4:
        return DAILY
    raise ValueError(f"bars are {gap:g} days apart: set [analytics] periods_per_year explicitly")


def _excess(r: np.ndarray, rf: np.ndarray | None) -> np.ndarray:
    r = np.asarray(r, dtype=float)
    return r if rf is None else r - np.asarray(rf, dtype=float)


def annual_return(r: np.ndarray, periods: int = DAILY) -> float:
    r = np.asarray(r, dtype=float)
    return float(np.mean(r)) * periods if len(r) else 0.0


def volatility(r: np.ndarray, periods: int = DAILY) -> float:
    r = np.asarray(r, dtype=float)
    return float(np.std(r, ddof=1)) * math.sqrt(periods) if len(r) > 1 else 0.0


def sharpe(r: np.ndarray, rf: np.ndarray | None = None, periods: int = DAILY) -> float:
    x = _excess(r, rf)
    if len(x) < 2:
        return 0.0
    sd = float(np.std(x, ddof=1))
    return 0.0 if sd == 0.0 else float(np.mean(x)) / sd * math.sqrt(periods)


def sortino(r: np.ndarray, rf: np.ndarray | None = None, periods: int = DAILY) -> float | None:
    x = _excess(r, rf)
    if len(x) == 0:
        return None
    downside = math.sqrt(float(np.mean(np.minimum(x, 0.0) ** 2)))
    return None if downside == 0.0 else float(np.mean(x)) / downside * math.sqrt(periods)


@dataclass(frozen=True)
class Drawdown:
    max_drawdown: float     # fraction of the peak
    peak: int
    trough: int
    recovery: int | None    # first index back at the peak, None if never
    duration: int           # bars from the peak to the recovery (or to the end)
    recovered: bool


def drawdown(equity: np.ndarray) -> Drawdown:
    """The deepest peak-to-trough fall of ``equity`` (index 0 is the starting capital)."""
    e = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(e)
    dd = (peak - e) / peak
    trough = int(np.argmax(dd))
    if dd[trough] == 0.0:
        return Drawdown(0.0, 0, 0, 0, 0, True)
    p = int(np.flatnonzero(e[: trough + 1] == peak[trough])[0])
    after = np.flatnonzero(e[trough:] >= peak[trough])
    recovery = int(trough + after[0]) if len(after) else None
    end = recovery if recovery is not None else len(e) - 1
    return Drawdown(float(dd[trough]), p, trough, recovery, end - p, recovery is not None)


def calmar(r: np.ndarray, equity: np.ndarray, periods: int = DAILY) -> float | None:
    dd = drawdown(equity).max_drawdown
    return None if dd == 0.0 else annual_return(r, periods) / dd


def _moments(x: np.ndarray) -> tuple[float, float, float]:
    """Per-period Sharpe (ddof = 1), skewness and (non-excess) kurtosis."""
    sd = float(np.std(x, ddof=1))
    sr = float(np.mean(x)) / sd if sd else 0.0
    c = x - np.mean(x)
    m2 = float(np.mean(c ** 2))
    if m2 == 0.0:
        return sr, 0.0, 3.0
    return sr, float(np.mean(c ** 3)) / m2 ** 1.5, float(np.mean(c ** 4)) / m2 ** 2


def sharpe_se(r: np.ndarray, rf: np.ndarray | None = None, periods: int = DAILY) -> dict[str, float]:
    x = _excess(r, rf)
    T = len(x)
    if T < 3:
        return {"iid": math.nan, "mertens": math.nan}
    sr, g3, g4 = _moments(x)
    iid = math.sqrt((1 + sr * sr / 2) / T)
    mertens = math.sqrt(max(1 + sr * sr / 2 - g3 * sr + (g4 - 3) / 4 * sr * sr, 0.0) / T)
    return {"iid": iid * math.sqrt(periods), "mertens": mertens * math.sqrt(periods)}


def psr(r: np.ndarray, sr0: float = 0.0, rf: np.ndarray | None = None) -> float:
    """P(true per-period Sharpe > ``sr0``)."""
    x = _excess(r, rf)
    T = len(x)
    if T < 3:
        return math.nan
    sr, g3, g4 = _moments(x)
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    if denom <= 0:
        return math.nan
    return NormalDist().cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(denom))


def dsr(r: np.ndarray, n_trials: int, rf: np.ndarray | None = None, periods: int = DAILY) -> dict[str, float]:
    """The deflated Sharpe: PSR against the expected best of ``n_trials`` null Sharpes."""
    x = _excess(r, rf)
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1 or len(x) < 3:
        sr0 = 0.0
    else:
        z = NormalDist().inv_cdf
        sr0 = math.sqrt(1 / (len(x) - 1)) * ((1 - EULER_GAMMA) * z(1 - 1 / n_trials)
                                            + EULER_GAMMA * z(1 - 1 / (n_trials * math.e)))
    return {"sr0_annual": sr0 * math.sqrt(periods), "probability": psr(x, sr0)}


def beta(r: np.ndarray, b: np.ndarray, rf: np.ndarray | None = None, periods: int = DAILY) -> dict[str, Any]:
    """OLS of excess returns on the benchmark's excess returns, over periods where both
    exist: beta, annualized alpha (intercept), correlation."""
    x, y = _excess(b, rf), _excess(r, rf)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = int(len(x))
    if n < 3 or float(np.var(x)) == 0.0:
        return {"beta": math.nan, "alpha_annual": math.nan, "correlation": math.nan, "n": n}
    cov = float(np.cov(y, x, ddof=1)[0, 1])
    bx = cov / float(np.var(x, ddof=1))
    sy = float(np.std(y, ddof=1))
    corr = cov / (float(np.std(x, ddof=1)) * sy) if sy else math.nan
    return {"beta": bx, "alpha_annual": (float(np.mean(y)) - bx * float(np.mean(x))) * periods,
            "correlation": corr, "n": n}


# --- Trades -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Episode:
    """A run of bars a symbol is held with one sign: opened on ``start``, closed on ``end``."""

    symbol: int
    start: int
    end: int
    gross: int
    net: int

    @property
    def duration(self) -> int:
        return self.end - self.start


def episodes(held: np.ndarray, pos: np.ndarray, old_leg: np.ndarray, new_leg: np.ndarray,
             costs: np.ndarray, close_at_end: bool = False) -> tuple[list[Episode], int]:
    """Closed episodes per symbol, and how many were still open at the end.

    Per bar, ``old_leg`` is the P&L of the position held into the bar and belongs to its
    episode; ``new_leg`` of the position after the bar's trades belongs to that one. A
    trade's costs go to the episode it closes or opens; a flip splits them by size. With
    ``close_at_end`` (the window was liquidated at its last close) an episode still held
    after the last bar's trades closes there."""
    n, N = held.shape
    out: list[Episode] = []
    still_open = 0
    for j in range(N):
        sign = 0
        start, gross, net = 0, 0, 0
        for t in range(n):
            h, p = int(held[t, j]), int(pos[t, j])
            o, w, c = int(old_leg[t, j]), int(new_leg[t, j]), int(costs[t, j])
            sh, sp = (h > 0) - (h < 0), (p > 0) - (p < 0)
            if sh != 0:
                gross += o
                net += o
            if sh != sp:
                if sh != 0:
                    cost_old = c if sp == 0 else c * abs(h) // (abs(h) + abs(p))
                    net -= cost_old
                    out.append(Episode(j, start, t, gross, net))
                    c -= cost_old
                if sp != 0:
                    start, gross, net = t, w, w - c
            else:
                gross += w
                net += w - c
            sign = sp
        if sign != 0:
            if close_at_end:
                out.append(Episode(j, start, n - 1, gross, net))
            else:
                still_open += 1
    out.sort(key=lambda e: (e.end, e.symbol))
    return out, still_open


def win_stats(pnls: list[int] | np.ndarray) -> dict[str, Any]:
    x = np.asarray(pnls, dtype=float)
    wins, losses = x[x > 0], x[x < 0]
    return {
        "n": int(len(x)),
        "hit_rate": float(len(wins)) / len(x) if len(x) else None,
        "avg_win": float(np.mean(wins)) if len(wins) else None,
        "avg_loss": float(np.mean(losses)) if len(losses) else None,
        "profit_factor": float(wins.sum()) / float(-losses.sum()) if len(losses) else None,
    }


def holding_period(trades: list[tuple[int, int, int]], split_ratio: np.ndarray) -> float | None:
    """Share-weighted bars between opening and closing each share, first in first out.
    ``trades`` are (bar, symbol, signed qty) in bar order; lots follow splits."""
    lots: dict[int, list[list[float]]] = {}   # symbol -> [[open bar, signed shares], ...]
    split_ratio = np.asarray(split_ratio, dtype=float)
    weighted, shares = 0.0, 0.0
    order = sorted(range(len(trades)), key=lambda k: trades[k][0])
    last_bar = 0
    for k in order:
        bar, sym, qty = trades[k]
        for b in range(last_bar + 1, bar + 1):
            for j, queue in lots.items():
                r = split_ratio[b, j]
                if r != 1.0:
                    for lot in queue:
                        lot[1] *= r
        last_bar = max(last_bar, bar)
        queue = lots.setdefault(sym, [])
        left = float(qty)
        while left and queue and (queue[0][1] > 0) != (left > 0):
            take = min(abs(left), abs(queue[0][1]))
            weighted += take * (bar - queue[0][0])
            shares += take
            queue[0][1] += take if queue[0][1] < 0 else -take
            left += -take if left > 0 else take
            if queue[0][1] == 0:
                queue.pop(0)
        if left:
            queue.append([bar, left])
    return weighted / shares if shares else None


def turnover(traded_notional: float, capital: float, n: int, periods: int = DAILY) -> float:
    """Traded notional (buys plus sells) over capital, a year."""
    return float(traded_notional) / capital * periods / n if n else 0.0


def exposure(gross: np.ndarray, net: np.ndarray, capital: float) -> dict[str, float]:
    g, s = np.asarray(gross, dtype=float), np.asarray(net, dtype=float)
    if len(g) == 0:
        return {"gross": 0.0, "net": 0.0, "time_in_market": 0.0}
    return {"gross": float(np.mean(g)) / capital, "net": float(np.mean(s)) / capital,
            "time_in_market": float(np.mean(g > 0))}


def cost_drag(gross_excess: np.ndarray, costs: np.ndarray, capital: float, periods: int = DAILY) -> dict[str, Any]:
    """Annual cost over capital against the annual gross excess return, and the multiple
    of today's costs that would bring the net excess return to zero."""
    n = len(gross_excess)
    annual_cost = float(np.sum(costs)) / capital * periods / n if n else 0.0
    gross = float(np.mean(gross_excess)) * periods if n else 0.0
    return {"annual_cost": annual_cost, "gross_excess_annual": gross,
            "break_even_multiple": gross / annual_cost if annual_cost > 0 else None}
