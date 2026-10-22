"""Normalized dataset kinds and their validation (M8).

Three kinds, all integers: prices in ticks of the instrument (bars: micro-dollars),
sizes in lots, timestamps in nanoseconds since the Unix epoch.

- ``trades``: one row per print, with the aggressor's side.
- ``quotes``: the top of book after each change (L1). An empty side is price 0, size 0.
- ``bars``: daily OHLCV per symbol, **unadjusted**, with the corporate actions of the
  day (ex-date dividend per share, split ratio) as columns. Adjusted prices are not
  stored: back-adjustment rewrites past prices with future splits and dividends.

``validate`` runs on every load and every write (``research.data.store``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BUY, SELL = 0, 1

COLUMNS: dict[str, dict[str, str]] = {
    "trades": {"ts": "int64", "trade_id": "int64", "price": "int64", "qty": "int64", "aggressor": "int8"},
    "quotes": {"ts": "int64", "bid_px": "int64", "bid_qty": "int64", "ask_px": "int64", "ask_qty": "int64"},
    "bars": {"symbol": "str", "date": "datetime64[ns]", "open": "int64", "high": "int64", "low": "int64",
             "close": "int64", "volume": "int64", "dividend": "int64", "split_num": "int64",
             "split_den": "int64"},
}
KINDS = tuple(COLUMNS)

# Bars are priced in micro-dollars: every US equity price and dividend Tiingo publishes
# fits exactly, and int64 still holds trillions of dollars.
BAR_PRICE_UNIT = "0.000001"


class ValidationError(ValueError):
    """A dataset that breaks its kind's rules. The message names the first bad row."""


@dataclass(frozen=True)
class Instrument:
    """What turns a vendor's decimals into integers: price tick and size lot."""

    symbol: str
    tick_size: str
    lot_size: str

    def to_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol, "tick_size": self.tick_size, "lot_size": self.lot_size}


def normalize_bars_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Bars in the canonical column order and dtypes, with the optional columns
    (volume, actions) filled in: no dividend, a 1:1 split, zero volume."""
    df = df.copy()
    for col, default in (("volume", 0), ("dividend", 0), ("split_num", 1), ("split_den", 1)):
        if col not in df.columns:
            df[col] = default
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    # No sorting: out-of-order rows are the validator's to reject, not ours to repair.
    df = df[list(COLUMNS["bars"])].astype({c: t for c, t in COLUMNS["bars"].items() if t != "datetime64[ns]"})
    return df.reset_index(drop=True)


def _first(mask: np.ndarray) -> int | None:
    idx = np.flatnonzero(mask)
    return int(idx[0]) if len(idx) else None


def _check(mask: np.ndarray, message: str) -> None:
    row = _first(mask)
    if row is not None:
        raise ValidationError(f"{message} (row {row})")


def _check_columns(df: pd.DataFrame, kind: str) -> None:
    expected = COLUMNS[kind]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValidationError(f"{kind}: missing column(s) {', '.join(missing)}")
    extra = [c for c in df.columns if c not in expected]
    if extra:
        raise ValidationError(f"{kind}: unexpected column(s) {', '.join(extra)}")
    for col, dtype in expected.items():
        actual = df[col].dtype
        if dtype == "str":
            ok = pd.api.types.is_string_dtype(actual) or actual == object
        else:
            ok = actual == np.dtype(dtype)
        if not ok:
            raise ValidationError(f"{kind}: column {col} is {actual}, expected {dtype}")


def validate(df: pd.DataFrame, kind: str) -> None:
    """Raise ``ValidationError`` unless ``df`` is a valid dataset of ``kind``."""
    if kind not in COLUMNS:
        raise ValidationError(f"unknown kind {kind!r}")
    _check_columns(df, kind)
    if kind == "trades":
        _validate_trades(df)
    elif kind == "quotes":
        _validate_quotes(df)
    else:
        _validate_bars(df)


def _monotonic(ts: np.ndarray) -> None:
    if len(ts) > 1:
        bad = np.flatnonzero(ts[1:] < ts[:-1])
        if len(bad):
            raise ValidationError(f"timestamp goes backwards at row {int(bad[0]) + 1}")


def _validate_trades(df: pd.DataFrame) -> None:
    _monotonic(df["ts"].to_numpy())
    qty, price = df["qty"].to_numpy(), df["price"].to_numpy()
    _check(qty < 0, "negative qty")
    _check(qty == 0, "zero qty")
    _check(price <= 0, "price must be positive")
    _check(~np.isin(df["aggressor"].to_numpy(), (BUY, SELL)), "aggressor must be 0 (buy) or 1 (sell)")
    ids = df["trade_id"].to_numpy()
    if len(ids) > 1:
        _check(np.concatenate([[False], ids[1:] <= ids[:-1]]), "trade_id must strictly increase")


def _validate_quotes(df: pd.DataFrame) -> None:
    _monotonic(df["ts"].to_numpy())
    for side in ("bid", "ask"):
        px, qty = df[f"{side}_px"].to_numpy(), df[f"{side}_qty"].to_numpy()
        _check(qty < 0, f"negative {side}_qty")
        _check(px < 0, f"negative {side}_px")
        _check((px > 0) != (qty > 0), f"{side}: a price needs a size, and an empty side is price 0 size 0")
    bid, ask = df["bid_px"].to_numpy(), df["ask_px"].to_numpy()
    # Locked (bid == ask) counts as crossed: replayed into the engine, the two
    # historical orders would trade with each other.
    _check((bid > 0) & (ask > 0) & (bid >= ask), "crossed or locked quote (bid >= ask)")


def _validate_bars(df: pd.DataFrame) -> None:
    sym = df["symbol"].to_numpy()
    dates = df["date"].to_numpy()
    if len(df) > 1:
        same = sym[1:] == sym[:-1]
        _check(np.concatenate([[False], same & (dates[1:] <= dates[:-1])]),
               "date must strictly increase within a symbol (sorted by symbol, date)")
        # Each symbol in one contiguous run: sorted by symbol.
        _check(np.concatenate([[False], sym[1:] < sym[:-1]]), "rows must be sorted by symbol, then date")
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    _check((o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0), "price must be positive")
    _check(h < np.maximum(o, c), "high below open or close")
    _check(lo > np.minimum(o, c), "low above open or close")
    _check(df["volume"].to_numpy() < 0, "negative volume")
    _check(df["dividend"].to_numpy() < 0, "negative dividend")
    _check((df["split_num"].to_numpy() <= 0) | (df["split_den"].to_numpy() <= 0),
           "split ratio must be positive")
