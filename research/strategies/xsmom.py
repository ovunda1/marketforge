"""Cross-sectional momentum (SPEC strategy 2), on daily bars.

Rank the universe by total return from ``lookback_months`` month-ends ago to
``skip_months`` month-ends ago (12-1: a year, skipping the most recent month, which
tends to reverse). Long the top ``quantile`` equally, short the bottom equally, half the
gross on each side, held until the next rebalance.

**When is "month end"?** Knowing that today is the last trading day of the month needs
tomorrow's date. So a month end is recognized on the first bar of the next month, which
is when the strategy rebalances, using closes up to the previous bar. Ties rank by
symbol name.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.backtest.bars import BarPanel


@dataclass(frozen=True)
class CrossSectionalMomentum:
    lookback_months: int = 12
    skip_months: int = 1
    quantile: float = 0.2

    def __post_init__(self) -> None:
        if not 0 <= self.skip_months < self.lookback_months or not 0 < self.quantile <= 0.5:
            raise ValueError(f"invalid parameters: {self}")

    def weights(self, panel: BarPanel) -> np.ndarray:
        tr = panel.total_return_index()
        T, N = tr.shape
        out = np.zeros((T, N))
        months = panel.dates.astype("datetime64[M]").astype(np.int64)
        ends: list[int] = []
        current = np.zeros(N)
        for t in range(T):
            if t > 0 and months[t] != months[t - 1]:
                ends.append(t - 1)  # bar t - 1 closed its month: known now, at bar t
                k = len(ends) - 1
                if k >= self.lookback_months:
                    current = self._rank(tr[ends[k - self.lookback_months]], tr[ends[k - self.skip_months]],
                                         panel.valid[t], panel.symbols)
            out[t] = current
        return out

    def _rank(self, start: np.ndarray, end: np.ndarray, tradable: np.ndarray, symbols: tuple[str, ...]) -> np.ndarray:
        ret = end / start - 1
        ok = np.flatnonzero(np.isfinite(ret) & tradable)
        n = int(len(ok) * self.quantile)
        w = np.zeros(len(symbols))
        if n < 1 or len(ok) < 2 * n:
            return w
        order = sorted(ok, key=lambda j: (-ret[j], symbols[j]))
        w[order[:n]] = 0.5 / n
        w[order[-n:]] = -0.5 / n
        return w
