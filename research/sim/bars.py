"""Synthetic daily bars (M8): offline research data with splits and dividends.

Each symbol's close follows ``c[t] = c[t-1] * (1 + mu[t] + vol * eps[t])``, where the drift
``mu`` is itself a slow AR(1) process, so trends persist and momentum has something to
find. Ex-dividend days drop the close by the dividend; split days divide it by the
ratio, as unadjusted prices do.

Only additions, multiplications and rounding to cents are used on the prices (no
``exp``/``log``), so the output is bit-identical on any IEEE-754 machine: run records
pin generated data by content hash.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.data import schema

MICROS_PER_CENT = 10_000


def _cents(x: np.ndarray) -> np.ndarray:
    return np.maximum(np.round(x * 100), 1).astype(np.int64)


def generate_bars(seed: int, symbols: tuple[str, ...] | list[str], n_days: int, start: str, *,
                  split_every: int = 0, dividend_every: int = 0, drift: float = 0.0002, vol: float = 0.015,
                  persistence: float = 0.98, drift_vol: float = 0.0004) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n_days)
    frames = []
    for j, symbol in enumerate(sorted(symbols)):
        eps, eta, gap = rng.standard_normal(n_days), rng.standard_normal(n_days), rng.standard_normal(n_days)
        wick = np.abs(rng.standard_normal((n_days, 2)))
        close = np.empty(n_days)
        opens = np.empty(n_days)
        dividend = np.zeros(n_days, dtype=np.int64)
        split = np.ones(n_days, dtype=np.int64)
        c = float(rng.integers(20, 200))
        mu = drift
        for t in range(n_days):
            prev = c
            mu = drift + persistence * (mu - drift) + drift_vol * eta[t]
            c = prev * (1.0 + mu + vol * eps[t])
            opens[t] = prev * (1.0 + 0.3 * vol * gap[t])
            if dividend_every and t > 0 and (t + 7 * j) % dividend_every == 0:
                d = round(prev * 0.005, 2)
                dividend[t] = int(round(d * 100)) * MICROS_PER_CENT
                c -= d
                opens[t] -= d
            if split_every and t > 0 and (t + 11 * j) % split_every == 0:
                split[t] = 2
                c /= 2.0
                opens[t] /= 2.0
            c = max(c, 0.05)
            opens[t] = max(opens[t], 0.05)
            close[t] = c
        o, cl = _cents(opens), _cents(close)
        hi = np.maximum(_cents(np.maximum(opens, close) * (1.0 + 0.004 * wick[:, 0])), np.maximum(o, cl))
        lo = np.minimum(_cents(np.minimum(opens, close) * (1.0 - 0.004 * wick[:, 1])), np.minimum(o, cl))
        lo = np.maximum(lo, 1)
        volume = rng.integers(100_000, 5_000_000, size=n_days)
        frames.append(pd.DataFrame({
            "symbol": symbol, "date": dates, "open": o * MICROS_PER_CENT, "high": hi * MICROS_PER_CENT,
            "low": lo * MICROS_PER_CENT, "close": cl * MICROS_PER_CENT, "volume": volume,
            "dividend": dividend, "split_num": split, "split_den": 1,
        }))
    df = schema.normalize_bars_frame(pd.concat(frames, ignore_index=True))
    schema.validate(df, "bars")
    return df
