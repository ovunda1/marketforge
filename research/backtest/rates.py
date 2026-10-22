"""The risk-free rate in a backtest (M9), from a ``rates`` dataset of FRED DTB3.

**From the quote to a daily rate.** DTB3 is the 3-month T-bill rate on a bank-discount
basis: a bill quoted at ``d`` (a fraction a year, 360-day basis) costs ``1 - d * 91/360``
for 1 paid in 91 days. Holding it earns ``1/price - 1`` over 91 calendar days, which per
calendar day is ``d / (360 - 91 d)``, simple. With ``d`` in basis points that is exactly
``bp / (3_600_000 - 91 bp)``, a rational number: no float touches money.

**Accrual.** Cash held from bar ``e - 1`` to bar ``e`` earns the calendar days between
their dates (a weekend earns three) at the rate known at bar ``e - 1``: the last DTB3
observation dated on or before it. Days without a quote carry the last known rate
forward; nothing is ever filled backward, so a bar before the first observation is an
error. Interest is rounded against the holder (a balance earns the floor, a debit pays
the ceiling), as dividends are.

Without a rates dataset the rate is 0 (``RiskFree.zero``) and the run says so.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

import numpy as np
import pandas as pd

BILL_DAYS = 91
YEAR_BASIS = 360


class RiskFree:
    def __init__(self, dates: np.ndarray | None, rate_bp: np.ndarray | None, source: str) -> None:
        self._dates = dates          # datetime64[D], increasing; None for zero
        self._rate = rate_bp
        self.source = source

    @classmethod
    def zero(cls) -> RiskFree:
        return cls(None, None, "none: 0")

    @classmethod
    def from_frame(cls, df: pd.DataFrame, source: str = "rates dataset") -> RiskFree:
        dates = df["date"].to_numpy().astype("datetime64[D]")
        if len(dates) == 0:
            raise ValueError("the rates dataset is empty")
        return cls(dates, df["rate"].to_numpy().astype(np.int64), source)

    @staticmethod
    def daily_rate(bp: int) -> Fraction:
        """One calendar day's simple return at a discount yield of ``bp`` basis points."""
        bp = int(bp)
        return Fraction(bp, YEAR_BASIS * 10_000 - BILL_DAYS * bp)

    def per_bar(self, dates: np.ndarray) -> list[Fraction]:
        """The accrual factor of each bar: 0 for the first, then the return of cash held
        since the previous bar."""
        d = np.asarray(dates).astype("datetime64[D]")
        out = [Fraction(0)]
        if self._dates is None:
            return out + [Fraction(0)] * (len(d) - 1)
        known = np.searchsorted(self._dates, d, side="right") - 1  # last observation <= each date
        for e in range(1, len(d)):
            i = int(known[e - 1])
            if i < 0:
                raise ValueError(f"no risk-free rate known on {d[e - 1]} (first observation {self._dates[0]}); "
                                 "rates are never back-filled")
            days = int((d[e] - d[e - 1]).astype(np.int64))
            out.append(days * self.daily_rate(int(self._rate[i])))
        return out

    def returns(self, dates: np.ndarray) -> np.ndarray:
        return np.array([float(f) for f in self.per_bar(dates)])

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source}
        if self._dates is not None:
            out.update(series="DTB3 (3-month T-bill, discount basis)",
                       daily_rate="bp / (3_600_000 - 91 bp) per calendar day, simple",
                       known_at="last observation on or before the previous bar, carried forward, never back-filled",
                       first=str(self._dates[0]), last=str(self._dates[-1]))
        return out


def accrue(amount: int, rate: Fraction, *, cost: bool = False) -> int:
    """``amount * rate`` in whole money units, rounded against the holder: a balance (or
    rebate) gets the floor, a debit or a ``cost`` the ceiling."""
    x = amount * rate
    return math.ceil(x) if cost else math.floor(x)
