"""The bar backtester for fast parameter sweeps (M8).

Input: a ``BarPanel`` and target weights ``w[t, j]`` decided at the close of bar ``t``
(from bars ``<= t`` only; ``research.backtest.causality`` checks that). Execution rules:

- A weight trades at bar ``t + lag``, ``lag >= 1`` (default 1), at that bar's open
  (default) or close. Trading on the bar that produced the signal is impossible to
  configure: that is the look-ahead the lag exists to prevent.
- Target shares are ``trunc(w * capital / close[t])``, sized off the fixed starting
  capital (no compounding) with the signal bar's close, truncated toward zero: whole
  shares, never more than the weight asks for.
- Shares are re-sized only when a symbol's weight changes, so a constant weight is held
  as a constant share count (no daily rebalancing to a fixed weight).
- A symbol without a bar on the execution day trades on its next bar.
- Splits scale positions (and pending targets) on the ex-date, fractional shares paid in
  cash at the close; dividends are paid on shares held into the ex-date.

Everything that is money or shares is int64; floats only size positions and summarize.
The loop runs once per bar with NumPy across symbols.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.backtest.bars import BarPanel
from research.backtest.costs import CostModel, TradeRecord, ZeroCost


@dataclass(frozen=True)
class BarExecution:
    lag: int = 1
    price: str = "open"

    def __post_init__(self) -> None:
        if not isinstance(self.lag, int) or self.lag < 1:
            raise ValueError(f"lag must be an integer >= 1 (a signal at the close of t trades at t+lag), got {self.lag}")
        if self.price not in ("open", "close"):
            raise ValueError(f"fill price must be 'open' or 'close', not {self.price!r}")


@dataclass
class BarResult:
    dates: np.ndarray
    equity: np.ndarray       # int64, gross, marked at the close
    pnl: np.ndarray          # int64, gross, per bar
    net_equity: np.ndarray   # int64, after costs
    net_pnl_per_bar: np.ndarray
    positions: np.ndarray    # int64 [bars, N] after the bar's trades
    trades: list[TradeRecord]
    costs: int
    gross_pnl: int
    net_pnl: int
    capital: int

    @property
    def returns(self) -> np.ndarray:
        """Net per-bar returns on the starting capital."""
        return self.net_pnl_per_bar / self.capital


def _split(qty: np.ndarray, num: np.ndarray, den: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shares after a num:den split, truncated toward zero, and the remainder in
    1/den new-share units (signed)."""
    scaled = np.abs(qty) * num
    whole = scaled // den
    return np.sign(qty) * whole, np.sign(qty) * (scaled - whole * den)


def run_vectorized(panel: BarPanel, weights: np.ndarray, capital: int, execution: BarExecution = BarExecution(),
                   cost_model: CostModel = ZeroCost(), start: int = 0, end: int | None = None) -> BarResult:
    T, N = panel.shape
    end = T if end is None else end
    weights = np.asarray(weights, dtype=float)
    if weights.shape != (T, N):
        raise ValueError(f"weights have shape {weights.shape}, the panel {(T, N)}")
    if not np.isfinite(weights).all():
        raise ValueError("weights must be finite (use 0 for no position)")
    if not 0 <= start < end <= T:
        raise ValueError(f"bad window [{start}, {end}) for {T} bars")

    lag = execution.lag
    fill = panel.open if execution.price == "open" else panel.close
    pos = np.zeros(N, np.int64)
    applied = np.zeros(N)                    # the weight each position was last sized from
    pending = np.zeros(N, np.int64)          # target shares waiting for a bar to trade on
    has_pending = np.zeros(N, bool)
    last_close = np.zeros(N, np.int64)
    cash = int(capital)
    n = end - start
    equity = np.zeros(n, np.int64)
    positions = np.zeros((n, N), np.int64)
    trades: list[TradeRecord] = []

    for e in range(start, end):
        valid = panel.valid[e]
        # Corporate actions on shares held into today.
        splits = valid & (panel.split_num[e] != panel.split_den[e])
        if splits.any():
            num, den = panel.split_num[e], panel.split_den[e]
            new_pos, rem = _split(pos, num, den)
            cash += int(np.sum(np.where(splits, rem * panel.close[e] // den, 0)))
            pos = np.where(splits, new_pos, pos)
            pending = np.where(splits & has_pending, _split(pending, num, den)[0], pending)
        cash += int(np.sum(np.where(valid, pos * panel.dividend[e], 0)))
        # Signals from the close of e - lag.
        s = e - lag
        if s >= 0:
            w = weights[s]
            changed = (w != applied) & panel.valid[s]
            if changed.any():
                target = np.trunc(w * capital / np.where(panel.close[s] > 0, panel.close[s], 1)).astype(np.int64)
                pending = np.where(changed, target, pending)
                has_pending |= changed
                applied = np.where(changed, w, applied)
        # Execute what can trade today.
        ready = has_pending & valid
        for j in np.flatnonzero(ready):
            qty = int(pending[j] - pos[j])
            if qty:
                price = int(fill[e, j])
                cash -= qty * price
                trades.append(TradeRecord(symbol=int(j), qty=qty, price=price, bar=e,
                                          volume=int(panel.volume[e, j])))
        pos = np.where(ready, pending, pos)
        has_pending &= ~ready
        last_close = np.where(valid, panel.close[e], last_close)
        equity[e - start] = cash + int(np.sum(pos * last_close))
        positions[e - start] = pos

    costs = np.array(cost_model.costs(trades), dtype=np.int64)
    cost_by_bar = np.zeros(n, np.int64)
    for t, c in zip(trades, costs):
        cost_by_bar[t.bar - start] += c
    net_equity = equity - np.cumsum(cost_by_bar)
    pnl = np.diff(equity, prepend=capital)
    net_pnl_per_bar = np.diff(net_equity, prepend=capital)
    return BarResult(dates=panel.dates[start:end], equity=equity, pnl=pnl, net_equity=net_equity,
                     net_pnl_per_bar=net_pnl_per_bar, positions=positions, trades=trades, costs=int(costs.sum()),
                     gross_pnl=int(equity[-1] - capital), net_pnl=int(net_equity[-1] - capital), capital=capital)
