"""The worker's data catalog (M11): dates and symbols only, never a price."""

from __future__ import annotations

import json
from pathlib import Path

from research.data import store
from research.sim.bars import generate_bars
from services.worker.catalog import SYNTHETIC_BARS, build_catalog

ALLOWED = {"id", "label", "source", "real", "path", "rates", "benchmark", "survivorship", "start", "end", "calendar",
           "symbols", "synthetic_bars"}


def test_without_downloaded_data_only_the_synthetic_dataset_is_listed(tmp_path: Path) -> None:
    [syn] = build_catalog(tmp_path)["datasets"]
    assert syn["id"] == "synthetic" and syn["real"] is False and syn["synthetic_bars"] == SYNTHETIC_BARS
    assert len(syn["calendar"]) == SYNTHETIC_BARS["n_days"] and syn["calendar"] == sorted(syn["calendar"])
    assert [s["symbol"] for s in syn["symbols"]] == SYNTHETIC_BARS["symbols"]
    assert set(syn) <= ALLOWED


def test_a_bars_dataset_is_described_by_dates_and_symbols_only(tmp_path: Path) -> None:
    df = generate_bars(seed=3, symbols=["SPY", "TLT"], n_days=40, start="2020-01-02")
    df = df[~((df["symbol"] == "TLT") & (df["date"] < "2020-01-10"))]  # TLT lists later
    store.write(df, tmp_path / "tiingo-etf-daily" / "bars.parquet", "bars",
                {"source": "test", "survivorship": "test universe"})
    cat = build_catalog(tmp_path)
    ds = next(d for d in cat["datasets"] if d["id"] == "tiingo-etf-daily")
    assert ds["real"] is True and ds["rates"] is None and ds["survivorship"] == "test universe"
    assert {s["symbol"]: s["first"] for s in ds["symbols"]} == {"SPY": "2020-01-02", "TLT": "2020-01-10"}
    assert set(ds) <= ALLOWED
    text = json.dumps(cat)
    for price in df["close"].head(20):
        assert str(int(price)) not in text  # no price leaks into the catalog


def test_a_broken_dataset_is_left_out_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "tiingo-etf-daily").mkdir()
    (tmp_path / "tiingo-etf-daily" / "bars.parquet").write_bytes(b"not parquet")
    assert [d["id"] for d in build_catalog(tmp_path)["datasets"]] == ["synthetic"]
