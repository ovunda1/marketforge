"""The data catalog (M11): what the dashboard's backtest form may choose from.

The worker, the only service that sees the datasets, builds it at startup and stores it
in Redis (`mf:catalog`); the gateway serves it (`GET /backtests/catalog`) and validates
forms against it (services/common/backtest_form.py). Per dataset: its symbols with
first and last dates, and the trading calendar. Dates and symbols only: never a price
(Tiingo's terms forbid redistributing them, and the form needs none).

The synthetic dataset is always there. The Tiingo ETF dataset is there when
`make data-tiingo` has been run on the host (compose mounts data/normalized read-only);
the FRED risk-free rate is added to it when present.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("mf.worker")

CATALOG_VERSION = 1
# The synthetic universe of research/examples/tsmom_walkforward.toml: fictional symbols.
SYNTHETIC_BARS: dict[str, Any] = {"seed": 7, "symbols": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH",
                                                         "III", "JJJ"],
                                  "n_days": 2500, "start": "2012-01-03", "split_every": 400, "dividend_every": 63}


def describe_bars(df: Any) -> dict[str, Any]:
    """Symbols (first and last date each), the calendar, and the overall range."""
    dates = df["date"].dt.strftime("%Y-%m-%d")
    calendar = sorted(set(dates))
    groups = df.assign(d=dates).groupby("symbol")["d"]
    symbols = [{"symbol": str(sym), "first": str(g.min()), "last": str(g.max())} for sym, g in groups]
    return {"start": calendar[0], "end": calendar[-1], "calendar": calendar,
            "symbols": sorted(symbols, key=lambda s: s["symbol"])}


def build_catalog(data_root: Path) -> dict[str, Any]:
    from research.data import store
    from research.sim.bars import generate_bars

    datasets: list[dict[str, Any]] = []
    tiingo = data_root / "tiingo-etf-daily" / "bars.parquet"
    rates = data_root / "fred-dtb3" / "rates.parquet"
    if tiingo.is_file():
        try:
            ds = store.load(tiingo, "bars")
            datasets.append({"id": "tiingo-etf-daily", "label": "US ETFs, daily",
                             "source": "Tiingo end-of-day prices (historical data)", "real": True,
                             "path": str(tiingo), "rates": str(rates) if rates.is_file() else None,
                             "benchmark": "SPY", "survivorship": ds.meta.get("survivorship"),
                             **describe_bars(ds.df)})
        except Exception as e:  # noqa: BLE001 - a broken file leaves the dataset out, logged
            log.warning("tiingo dataset left out of the catalog: %s", e)
    df = generate_bars(**SYNTHETIC_BARS)
    datasets.append({"id": "synthetic", "label": "Synthetic daily bars",
                     "source": "generated (research/sim/bars.py, seed 7): fictional symbols", "real": False,
                     "synthetic_bars": SYNTHETIC_BARS, "rates": None, "benchmark": "AAA",
                     "survivorship": "synthetic: no symbol ever delists", **describe_bars(df)})
    return {"version": CATALOG_VERSION, "datasets": datasets}
