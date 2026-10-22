"""Binance public archive (data.binance.vision) to normalized trades and quotes (M8).

Files are daily CSVs inside zips, each published with a SHA-256 (``download.py`` checks
it). Formats handled:

- trades: ``id,price,qty,quote_qty,time,is_buyer_maker[,is_best_match]``, with or
  without a header (USD-M futures files have one, older spot files do not).
- bookTicker (USD-M futures): ``update_id,best_bid_price,best_bid_qty,best_ask_price,
  best_ask_qty,transaction_time,event_time``. ``transaction_time`` is when the matching
  engine changed the book, so it orders quotes against trades.

Timestamps are milliseconds, or microseconds in spot files from 2025; both become
nanoseconds. Decimals go through ``Decimal`` onto the instrument's tick and lot grid.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from research.data import schema
from research.data.schema import Instrument
from research.ticks import to_units

TRADE_FIELDS = ("id", "price", "qty", "quote_qty", "time", "is_buyer_maker")
BOOK_TICKER_FIELDS = ("update_id", "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty",
                      "transaction_time", "event_time")


def to_ns(value: int) -> int:
    """A Binance epoch timestamp in ms, us or ns, as ns (told apart by magnitude:
    any date from 2001 to 2286 has 13 digits in ms, 16 in us, 19 in ns)."""
    if 10**12 <= value < 10**13:
        return value * 1_000_000
    if 10**15 <= value < 10**16:
        return value * 1_000
    if 10**18 <= value < 10**19:
        return value
    raise ValueError(f"timestamp {value}: unknown unit (expected ms, us or ns since the epoch)")


def _rows(path: str | Path, header_first: str) -> Iterator[list[str]]:
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"{path}: expected one CSV inside, found {names}")
            text = io.TextIOWrapper(z.open(names[0]), encoding="ascii", newline="")
            yield from _csv_rows(text, header_first)
    else:
        with open(path, encoding="ascii", newline="") as f:
            yield from _csv_rows(f, header_first)


def _csv_rows(f: io.TextIOBase, header_first: str) -> Iterator[list[str]]:
    reader = csv.reader(f)
    for i, row in enumerate(reader):
        if i == 0 and row and row[0] == header_first:
            continue
        if row:
            yield row


def _bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered not in ("true", "false"):
        raise ValueError(f"not a boolean: {text!r}")
    return lowered == "true"


def read_trades_csv(path: str | Path, instrument: Instrument) -> pd.DataFrame:
    ts, ids, price, qty, aggressor = [], [], [], [], []
    for row in _rows(path, "id"):
        ids.append(int(row[0]))
        price.append(to_units(row[1], instrument.tick_size))
        qty.append(to_units(row[2], instrument.lot_size))
        ts.append(to_ns(int(row[4])))
        # The buyer was the resting (maker) side: the seller crossed the spread.
        aggressor.append(schema.SELL if _bool(row[5]) else schema.BUY)
    df = pd.DataFrame({"ts": ts, "trade_id": ids, "price": price, "qty": qty, "aggressor": aggressor})
    df = df.astype(schema.COLUMNS["trades"])
    schema.validate(df, "trades")
    return df


def read_book_ticker_csv(path: str | Path, instrument: Instrument) -> pd.DataFrame:
    cols: dict[str, list[int]] = {k: [] for k in schema.COLUMNS["quotes"]}
    for row in _rows(path, "update_id"):
        cols["bid_px"].append(to_units(row[1], instrument.tick_size))
        cols["bid_qty"].append(to_units(row[2], instrument.lot_size))
        cols["ask_px"].append(to_units(row[3], instrument.tick_size))
        cols["ask_qty"].append(to_units(row[4], instrument.lot_size))
        cols["ts"].append(to_ns(int(row[5])))
    df = pd.DataFrame(cols).astype(schema.COLUMNS["quotes"])
    schema.validate(df, "quotes")
    return df
