"""Dividends in 1e-8 dollars (M9): exact for every Tiingo divCash, paid to the micro-dollar
with the remainder rounded against the holder."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.backtest.bars import BarPanel
from research.backtest.vectorized import run_vectorized
from research.data import csvbars, schema, store, tiingo
from research.sim.bars import generate_bars
from research.ticks import OffGridError

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "research" / "data" / "manifests" / "tiingo-etf-daily.json"


def row(div: str) -> dict[str, str]:
    return {"date": "2020-01-02T00:00:00.000Z", "open": "10", "high": "10", "low": "10", "close": "10",
            "volume": "1", "divCash": div, "splitFactor": "1.0"}


def test_the_dividend_unit_is_finer_than_the_price_unit() -> None:
    assert schema.DIVIDEND_UNIT == "0.00000001"
    assert schema.DIVIDEND_UNITS_PER_PRICE_UNIT == 100


@pytest.mark.parametrize(("text", "units"), [("0.13050491", 13_050_491), ("4.44014886", 444_014_886),
                                             ("0.1228248", 12_282_480), ("0.25", 25_000_000), ("0", 0)])
def test_tiingo_dividends_parse_exactly(text: str, units: int) -> None:
    assert tiingo.to_bars("X", [row(text)])["dividend"].tolist() == [units]


def test_a_dividend_finer_than_the_unit_is_rejected() -> None:
    with pytest.raises(OffGridError):
        tiingo.to_bars("X", [row("0.123456789")])


def test_csv_dividends_use_the_dividend_unit(tmp_path) -> None:
    path = tmp_path / "b.csv"
    path.write_text("date,open,high,low,close,volume,dividend\n2020-01-02,10,10,10,10,1,0.13050491\n")
    assert csvbars.read_bars_csv(path, "X")["dividend"].tolist() == [13_050_491]


def test_synthetic_dividends_are_whole_cents_in_the_dividend_unit() -> None:
    df = generate_bars(3, ["A"], 200, "2015-01-02", dividend_every=60)
    paid = df.loc[df["dividend"] > 0, "dividend"]
    assert len(paid) and (paid % 1_000_000 == 0).all()


def one_symbol(dividend: int) -> BarPanel:
    rows = [{"symbol": "X", "date": d, "open": 10_000_000, "high": 10_000_000, "low": 10_000_000,
             "close": 10_000_000, "volume": 1, "dividend": dv, "split_num": 1, "split_den": 1}
            for d, dv in zip(pd.bdate_range("2020-01-01", periods=3), [0, 0, dividend])]
    return BarPanel.from_frame(schema.normalize_bars_frame(pd.DataFrame(rows)))


@pytest.mark.parametrize(("weight", "cash"), [
    # 100 shares x 0.13050491 = 13.050491 dollars: exactly 13_050_491 micro-dollars.
    (1.0, 13_050_491),
    # 3 shares x 0.13050491 = 0.39151473: a long gets the floor ...
    (0.03, 391_514),
    # ... and a short pays the ceiling (floor toward -inf).
    (-0.03, -391_515),
])
def test_dividend_cash_rounds_against_the_holder(weight: float, cash: int) -> None:
    p = one_symbol(13_050_491)
    w = np.full((3, 1), weight)
    r = run_vectorized(p, w, capital=1_000_000_000)
    no_div = run_vectorized(one_symbol(0), w, capital=1_000_000_000)
    assert int(r.equity[-1] - no_div.equity[-1]) == cash


def test_total_return_index_scales_the_dividend() -> None:
    p = one_symbol(25_000_000)  # 0.25 on a 10.00 close
    np.testing.assert_allclose(p.total_return_index()[:, 0], [1.0, 1.0, 1.025])


def test_stored_datasets_carry_the_new_schema_version(tmp_path) -> None:
    # Files written with micro-dollar dividends (schema 1) must not load as if exact.
    assert store.SCHEMA_VERSION == 2
    df = tiingo.to_bars("X", [row("0.5")])
    path = tmp_path / "b.parquet"
    store.write(df, path, "bars", {})
    assert store.load(path, "bars").df["dividend"].tolist() == [50_000_000]


def test_the_tiingo_manifest_pins_every_ticker() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert sorted(manifest["pins"]) == sorted(manifest["universe"])
    assert all(len(v) == 64 for v in manifest["pins"].values())
