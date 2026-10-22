"""Daily bars from a plain CSV (M8): for data you bring yourself.

Columns ``date,open,high,low,close,volume`` and optionally ``dividend`` (ex-date cash per
share) and ``split`` (new shares per old: ``2``, ``0.5``). Prices in dollars as decimal
text; stored in micro-dollars, unadjusted.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd

from research.data import schema
from research.data.tiingo import split_ratio
from research.ticks import to_units


def read_bars_csv(source: str | Path | io.TextIOBase, symbol: str) -> pd.DataFrame:
    if isinstance(source, (str, Path)):
        with open(source, encoding="utf-8", newline="") as f:
            return _read(f, symbol)
    return _read(source, symbol)


def _read(f: io.TextIOBase, symbol: str) -> pd.DataFrame:
    unit = schema.BAR_PRICE_UNIT
    rows = []
    for r in csv.DictReader(f):
        num, den = split_ratio(r.get("split") or "1")
        rows.append({
            "symbol": symbol, "date": r["date"],
            "open": to_units(r["open"], unit), "high": to_units(r["high"], unit),
            "low": to_units(r["low"], unit), "close": to_units(r["close"], unit),
            "volume": to_units(r["volume"], "1"), "dividend": to_units(r.get("dividend") or "0", schema.DIVIDEND_UNIT),
            "split_num": num, "split_den": den,
        })
    df = schema.normalize_bars_frame(pd.DataFrame(rows, columns=list(schema.COLUMNS["bars"])))
    schema.validate(df, "bars")
    return df
