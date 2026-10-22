"""The risk-free rate (M9): FRED DTB3 as a pinned ``rates`` dataset, and how the quoted
discount yield becomes interest on a bar's cash, point-in-time."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.backtest.rates import RiskFree, accrue
from research.data import download, fred, schema, store
from research.ticks import OffGridError

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "research" / "data" / "manifests" / "fred-dtb3.json"


class FakeTransport:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status, self.calls = body, status, []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, headers))
        return self.status, self.body


CSV = b"observation_date,DTB3\n2024-01-02,5.24\n2024-01-03,.\n2024-01-04,5.25\n"


# --- Parsing and storage -------------------------------------------------------------------------

def test_fred_csv_becomes_basis_points_with_missing_days_dropped() -> None:
    df = fred.to_rates(CSV.decode())
    schema.validate(df, "rates")
    assert [d.strftime("%Y-%m-%d") for d in df["date"]] == ["2024-01-02", "2024-01-04"]
    assert df["rate"].tolist() == [524, 525]


def test_an_empty_value_is_a_missing_day_too() -> None:
    # The current fredgraph.csv leaves the value empty; older files held ".".
    assert fred.to_rates("observation_date,DTB3\n2024-01-01,\n2024-01-02,5.24\n")["rate"].tolist() == [524]


def test_the_old_fred_header_is_accepted() -> None:
    assert fred.to_rates("DATE,DTB3\n2024-01-02,5.24\n")["rate"].tolist() == [524]


def test_a_rate_finer_than_a_basis_point_is_rejected() -> None:
    with pytest.raises(OffGridError):
        fred.to_rates("DATE,DTB3\n2024-01-02,5.245\n")


def test_rates_validation() -> None:
    good = fred.to_rates(CSV.decode())
    with pytest.raises(schema.ValidationError, match="increasing"):
        schema.validate(good.iloc[::-1].reset_index(drop=True), "rates")
    bad = good.copy()
    bad.loc[0, "rate"] = 60_000
    with pytest.raises(schema.ValidationError, match="rate"):
        schema.validate(bad, "rates")


def test_rates_round_trip_through_the_store(tmp_path) -> None:
    df = fred.to_rates(CSV.decode())
    store.write(df, tmp_path / "r.parquet", "rates", {"series": "DTB3"})
    assert store.load(tmp_path / "r.parquet", "rates").df["rate"].tolist() == [524, 525]


def test_the_client_asks_for_the_manifest_range_without_a_key() -> None:
    transport = FakeTransport(CSV)
    df = fred.FredClient(transport=transport).series("DTB3", "2006-12-01", "2024-12-31")
    assert len(df) == 2
    url, _ = transport.calls[0]
    assert "id=DTB3" in url and "cosd=2006-12-01" in url and "coed=2024-12-31" in url


def test_http_errors_are_clear() -> None:
    with pytest.raises(fred.FredError, match="503"):
        fred.FredClient(transport=FakeTransport(b"", 503)).series("DTB3", "2024-01-01", "2024-01-31")


def test_fetch_fred_checks_the_pin(tmp_path) -> None:
    manifest = {"name": "fred-dtb3", "source": "fred", "license": "x", "series": "DTB3", "start": "2024-01-01",
                "end": "2024-01-31", "pins": {}}
    client = fred.FredClient(transport=FakeTransport(CSV))
    with pytest.raises(download.PinMissing):
        download.fetch_fred(manifest, tmp_path, client)
    path = download.fetch_fred(manifest, tmp_path, client, update_pins=True)
    assert store.load(path, "rates").df["rate"].tolist() == [524, 525]
    assert download.fetch_fred(manifest, tmp_path, client) == path
    manifest["pins"]["DTB3"] = "0" * 64
    with pytest.raises(download.PinMismatch):
        download.fetch_fred(manifest, tmp_path, client)


def test_the_dtb3_manifest_covers_the_tiingo_range_and_is_pinned() -> None:
    manifest = download.load_manifest(MANIFEST)
    tiingo_start = json.loads((MANIFEST.parent / "tiingo-etf-daily.json").read_text())["start"]
    assert manifest["series"] == "DTB3"
    # The first bar's accrual needs a rate observed before it: no back-filling.
    assert manifest["start"] < tiingo_start and manifest["end"] >= "2024-12-31"
    assert len(manifest["pins"].get("DTB3", "")) == 64


# --- The quoted rate as interest ----------------------------------------------------------------------

def test_a_discount_yield_becomes_a_calendar_day_rate() -> None:
    # DTB3 is a bank-discount yield d on a 91-day bill, 360-day basis: price 1 - d*91/360,
    # so a day earns (1/price - 1)/91 = d / (360 - 91 d). In basis points:
    # 10_000 d / (3_600_000 - 91 * 10_000 d).
    assert RiskFree.daily_rate(500) == Fraction(500, 3_600_000 - 91 * 500)
    assert RiskFree.daily_rate(0) == 0


def test_interest_rounds_against_the_holder() -> None:
    rate = RiskFree.daily_rate(500)
    assert accrue(1_000_000_000, rate) == 140_666        # 140_666.76: a balance earns the floor
    assert accrue(-1_000_000_000, rate) == -140_667      # a debit pays the ceiling
    assert accrue(1_000_000_000, rate, cost=True) == 140_667  # a cost is never rounded down


def dates(*days: str) -> np.ndarray:
    return np.array(pd.to_datetime(list(days)).values.astype("datetime64[D]"))


def rf(obs: dict[str, int]) -> RiskFree:
    return RiskFree.from_frame(pd.DataFrame({"date": pd.to_datetime(list(obs)), "rate": list(obs.values())}))


def test_each_bar_accrues_calendar_days_at_the_rate_known_at_the_previous_bar() -> None:
    r = rf({"2024-01-05": 500, "2024-01-09": 400})
    # Fri -> Mon: 3 days at Friday's rate. Mon -> Tue: Monday has no observation, so
    # Friday's rate carries forward. Tue -> Wed: Tuesday's 400 (dated at the start of the
    # period). The rate dated Wednesday is not used for the period ending Wednesday.
    accr = r.per_bar(dates("2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"))
    assert accr == [Fraction(0), 3 * RiskFree.daily_rate(500), RiskFree.daily_rate(500), RiskFree.daily_rate(400)]


def test_a_rate_is_never_back_filled() -> None:
    r = rf({"2024-01-08": 500})
    with pytest.raises(ValueError, match="no risk-free rate"):
        r.per_bar(dates("2024-01-05", "2024-01-08"))


def test_without_a_rates_dataset_the_rate_is_zero() -> None:
    assert RiskFree.zero().per_bar(dates("2024-01-05", "2024-01-08")) == [0, 0]
    assert RiskFree.zero().describe()["source"] == "none: 0"


def test_per_bar_returns_are_the_accrual_as_floats() -> None:
    r = rf({"2024-01-05": 500})
    np.testing.assert_allclose(r.returns(dates("2024-01-05", "2024-01-08")), [0.0, float(3 * RiskFree.daily_rate(500))])
