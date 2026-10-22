"""The few metrics M8 needs to choose parameters and summarize a run. M9 adds the full
analytics (Sortino, Calmar, turnover, attribution...)."""

from __future__ import annotations

import numpy as np

PERIODS_PER_YEAR = 252


def sharpe(returns: np.ndarray, periods: int = PERIODS_PER_YEAR) -> float:
    """Annualized mean over standard deviation of per-period returns; 0 if flat."""
    r = np.asarray(returns, dtype=float)
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return 0.0 if sd == 0.0 else float(np.mean(r)) / sd * float(np.sqrt(periods))


def max_drawdown(equity: np.ndarray) -> float:
    """Largest peak-to-trough fall of ``equity``, as a fraction of the peak (>= 0)."""
    e = np.asarray(equity, dtype=float)
    if len(e) == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    return float(np.max((peak - e) / peak))


def summary(pnl: np.ndarray, capital: int) -> dict[str, float]:
    """Returns are P&L over the fixed starting capital (positions are sized off it)."""
    returns = np.asarray(pnl, dtype=float) / capital
    equity = capital + np.cumsum(np.asarray(pnl, dtype=np.int64))
    years = len(returns) / PERIODS_PER_YEAR
    total = float(equity[-1] / capital - 1) if len(equity) else 0.0
    return {
        "total_return": total,
        "annual_return": total / years if years else 0.0,
        "annual_volatility": float(np.std(returns, ddof=1) * np.sqrt(PERIODS_PER_YEAR)) if len(returns) > 1 else 0.0,
        "sharpe": sharpe(returns),
        "max_drawdown": max_drawdown(np.concatenate([[capital], equity])),
        "bars": float(len(returns)),
    }
