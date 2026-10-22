"""FRED daily rate series as a ``rates`` dataset (M9): the risk-free rate is DTB3, the
3-month Treasury bill secondary-market rate.

FRED's ``fredgraph.csv`` needs no API key. It has two columns, the date and the value in
percent with two decimals; days without a quote (holidays) are empty (``.`` in older files) and are dropped, so
a missing day is absent rather than invented. Filling it is the consumer's job, forward
only (``research.backtest.rates``). Values are parsed as text onto the basis-point grid,
never through a float.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

import pandas as pd

from research.data import schema
from research.ticks import to_units

API = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# (url, headers) -> (HTTP status, body), as in research.data.tiingo.
Transport = Callable[[str, dict[str, str]], tuple[int, bytes]]


class FredError(RuntimeError):
    pass


def _urllib_transport(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, b""


def to_rates(text: str) -> pd.DataFrame:
    """FRED CSV (``observation_date`` or the older ``DATE`` header) to a validated
    ``rates`` frame in basis points."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    if len(header) != 2 or header[0] not in ("observation_date", "DATE"):
        raise FredError(f"unexpected FRED CSV header {header}")
    rows = [{"date": d, "rate": to_units(v, schema.RATE_UNIT)} for d, v in reader if v.strip() not in (".", "")]
    df = schema.normalize_rates_frame(pd.DataFrame(rows, columns=list(schema.COLUMNS["rates"])))
    schema.validate(df, "rates")
    return df


class FredClient:
    def __init__(self, *, transport: Transport | None = None) -> None:
        self._transport = transport or _urllib_transport

    def series(self, series_id: str, start: str, end: str) -> pd.DataFrame:
        query = urllib.parse.urlencode({"id": series_id, "cosd": start, "coed": end})
        status, body = self._transport(f"{API}?{query}", {"User-Agent": "marketforge-research"})
        if status != 200:
            raise FredError(f"FRED returned HTTP {status} for {series_id}")
        return to_rates(body.decode("utf-8"))
