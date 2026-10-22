"""Daily bars as aligned (date x symbol) integer arrays (M8).

A symbol has a bar on a date or it does not (``valid``): before it lists, after it
delists, or on a missing day. Cells without a bar hold 0 (prices, volume, dividend) or a
1:1 split. ``upto(t)`` is the panel as it was known at the close of bar ``t``: a read-only
prefix, nothing after it. Strategies on bars receive panels like that.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np
import pandas as pd

from research.data import schema

ARRAYS = ("open", "high", "low", "close", "volume", "dividend", "split_num", "split_den")


def _frozen(a: np.ndarray) -> np.ndarray:
    a.flags.writeable = False
    return a


@dataclass(frozen=True)
class BarPanel:
    dates: np.ndarray       # datetime64[D], strictly increasing
    symbols: tuple[str, ...]
    open: np.ndarray        # int64 [T, N], micro-dollars
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    dividend: np.ndarray    # ex-date cash per share
    split_num: np.ndarray   # new shares per old: num / den
    split_den: np.ndarray
    valid: np.ndarray       # bool [T, N]

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> BarPanel:
        df = df.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
        schema.validate(df, "bars")
        dates = np.unique(df["date"].to_numpy().astype("datetime64[D]"))
        symbols = tuple(sorted(df["symbol"].astype(str).unique()))
        T, N = len(dates), len(symbols)
        rows = np.searchsorted(dates, df["date"].to_numpy().astype("datetime64[D]"))
        cols = np.searchsorted(np.array(symbols), df["symbol"].astype(str).to_numpy())
        arrays = {}
        for name in ARRAYS:
            a = np.ones((T, N), np.int64) if name.startswith("split") else np.zeros((T, N), np.int64)
            a[rows, cols] = df[name].to_numpy(np.int64)
            arrays[name] = _frozen(a)
        valid = np.zeros((T, N), bool)
        valid[rows, cols] = True
        return cls(dates=_frozen(dates), symbols=symbols, valid=_frozen(valid), **arrays)

    def upto(self, t: int) -> BarPanel:
        """Bars 0..t inclusive (``t`` may be negative, from the end)."""
        stop = t + 1 if t >= 0 else len(self.dates) + t + 1
        if not 0 < stop <= len(self.dates):
            raise IndexError(f"bar {t} out of range")
        kwargs = {f.name: getattr(self, f.name) for f in fields(self)}
        for name in (*ARRAYS, "valid", "dates"):
            kwargs[name] = kwargs[name][:stop]
        return BarPanel(**kwargs)

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape

    def total_return_index(self) -> np.ndarray:
        """Growth of one unit held from each symbol's first bar, dividends reinvested and
        splits applied on their ex-dates; NaN where there is no bar. Bar ``t`` uses only
        bars up to ``t``."""
        T, N = self.shape
        out = np.full((T, N), np.nan)
        last_close = np.zeros(N)
        level = np.full(N, np.nan)
        for t in range(T):
            v = self.valid[t]
            c = self.close[t].astype(float)
            ratio = self.split_num[t] / self.split_den[t]
            growth = (c * ratio + self.dividend[t] * ratio) / np.where(last_close > 0, last_close, 1.0)
            started = v & ~np.isnan(level)
            level = np.where(started, level * growth, level)
            level = np.where(v & np.isnan(level), 1.0, level)
            last_close = np.where(v, c, last_close)
            out[t] = np.where(v, level, np.nan)
        return out
