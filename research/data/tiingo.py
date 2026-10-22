"""Tiingo end-of-day prices to normalized, unadjusted daily bars (M8).

The API key comes from the ``TIINGO_API_KEY`` environment variable and nowhere else:
there is no key argument and no key file. It is sent only in the ``Authorization``
header, never in a URL, a log line, an error or a run record.

Only the raw fields are kept: ``open/high/low/close/volume`` as traded, ``divCash`` (the
ex-date dividend per share) and ``splitFactor`` (new shares per old, on the ex-date).
The ``adj*`` fields are dropped: they are rescaled by every later split and dividend,
so a past adjusted price depends on the future. JSON numbers are parsed as text so no
price passes through a float.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pandas as pd

from research.data import schema
from research.ticks import to_units

API = "https://api.tiingo.com/tiingo/daily"
ENV_KEY = "TIINGO_API_KEY"

# (url, headers) -> (HTTP status, body). Tests inject one; the default uses urllib.
Transport = Callable[[str, dict[str, str]], tuple[int, bytes]]


class TiingoError(RuntimeError):
    pass


class MissingApiKey(TiingoError):
    pass


def _urllib_transport(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, b""


def split_ratio(factor: str) -> tuple[int, int]:
    """Tiingo's split factor as (new shares, old shares): "4.0" -> (4, 1),
    "0.333333" (a 1-for-3 reverse split, rounded by the vendor) -> (1, 3)."""
    exact = Fraction(Decimal(factor))
    ratio = exact.limit_denominator(1000)
    if abs(ratio - exact) > Fraction(1, 10**5):
        raise ValueError(f"split factor {factor} is not a plausible ratio")
    return ratio.numerator, ratio.denominator


def to_bars(ticker: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Rows of Tiingo JSON (numbers as text) to a validated bars frame."""
    unit = schema.BAR_PRICE_UNIT
    out = []
    for r in rows:
        num, den = split_ratio(str(r["splitFactor"]))
        out.append({
            "symbol": ticker, "date": str(r["date"])[:10],
            "open": to_units(str(r["open"]), unit), "high": to_units(str(r["high"]), unit),
            "low": to_units(str(r["low"]), unit), "close": to_units(str(r["close"]), unit),
            "volume": to_units(str(r["volume"]), "1"), "dividend": to_units(str(r["divCash"]), unit),
            "split_num": num, "split_den": den,
        })
    df = schema.normalize_bars_frame(pd.DataFrame(out, columns=list(schema.COLUMNS["bars"])))
    schema.validate(df, "bars")
    return df


class TiingoClient:
    def __init__(self, *, transport: Transport | None = None) -> None:
        key = os.environ.get(ENV_KEY, "").strip()
        if not key:
            raise MissingApiKey(f"set {ENV_KEY} in the environment to download Tiingo data "
                                "(a free key from tiingo.com); everything else runs without it")
        self._key = key
        self._transport = transport or _urllib_transport

    def __repr__(self) -> str:
        return "TiingoClient(key=<from environment>)"

    def daily_bars(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        query = urllib.parse.urlencode({"startDate": start, "endDate": end, "format": "json"})
        url = f"{API}/{urllib.parse.quote(ticker)}/prices?{query}"
        status, body = self._transport(url, {"Authorization": f"Token {self._key}",
                                             "Content-Type": "application/json"})
        if status != 200:
            # The body may quote the key back; it is not included.
            raise TiingoError(f"Tiingo returned HTTP {status} for {ticker}")
        rows = json.loads(body, parse_float=str, parse_int=str)
        if not isinstance(rows, list):
            raise TiingoError(f"Tiingo returned an unexpected payload for {ticker}")
        return to_bars(ticker, rows)
