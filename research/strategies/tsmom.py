"""Time-series momentum (SPEC strategy 1), on daily bars.

For each symbol at the close of bar ``t``: the sign of its total return over the last
``lookback`` bars, scaled to ``target_vol`` by the annualized volatility of its last
``vol_window`` daily returns, capped at ``max_leverage``, and divided by the number of
symbols with a signal that day (equal risk budget per symbol).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.backtest.bars import BarPanel
from research.backtest.metrics import PERIODS_PER_YEAR


@dataclass(frozen=True)
class TimeSeriesMomentum:
    lookback: int = 250
    vol_window: int = 60
    target_vol: float = 0.10
    max_leverage: float = 2.0

    def __post_init__(self) -> None:
        if self.lookback < 1 or self.vol_window < 2 or self.target_vol <= 0 or self.max_leverage <= 0:
            raise ValueError(f"invalid parameters: {self}")

    def weights(self, panel: BarPanel) -> np.ndarray:
        tr = panel.total_return_index()
        T, N = tr.shape
        out = np.zeros((T, N))
        daily = np.full((T, N), np.nan)
        daily[1:] = tr[1:] / tr[:-1] - 1
        first = max(self.lookback, self.vol_window)
        for t in range(first, T):
            trend = tr[t] / tr[t - self.lookback] - 1
            # Returns of bars t - vol_window + 1 .. t; a NaN anywhere (no bar) means no signal.
            window = daily[t - self.vol_window + 1: t + 1]
            vol = np.std(window, axis=0, ddof=1) * np.sqrt(PERIODS_PER_YEAR)
            ok = np.isfinite(trend) & np.isfinite(vol)
            if not ok.any():
                continue
            scale = np.minimum(np.divide(self.target_vol, vol, out=np.full(N, np.inf), where=vol > 0),
                               self.max_leverage)
            out[t] = np.where(ok, np.sign(trend) * scale, 0.0) / ok.sum()
        return out
