"""The dashboard's backtest form (M11): validation against the data catalog (shared,
hand-written cases with dashboard/src/lib/backtestForm.test.ts) and the walk-forward
config it becomes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.runs import validate_config
from services.common.backtest_form import COSTS, FormInvalid, build_config, lookback_days, validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "backtest_form"
CATALOG = json.loads((FIXTURES / "catalog.json").read_text())
CASES = json.loads((FIXTURES / "cases.json").read_text())["cases"]
VALID = next(c["form"] for c in CASES if c["name"] == "valid tsmom")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_every_shared_case_reports_exactly_the_expected_fields(case: dict) -> None:
    errors = validate(case["form"], CATALOG)
    assert sorted({e.field for e in errors}) == case["error_fields"]
    assert all(e.message.endswith((".", ")")) and len(e.message) > 10 for e in errors)  # sentences, not codes


def test_the_range_message_says_how_many_days_are_there_and_needed() -> None:
    form = next(c["form"] for c in CASES if c["name"] == "one day short of train + test")
    [err] = validate(form, CATALOG)
    assert "1511 trading days" in err.message and "1512" in err.message


@pytest.mark.parametrize("case", [c for c in CASES if not c["error_fields"]], ids=lambda c: c["name"])
def test_a_valid_form_builds_a_config_the_backtester_accepts(case: dict) -> None:
    cfg = build_config(case["form"], CATALOG)
    canonical = validate_config(cfg)  # the research stack's own validation (M8)
    form = case["form"]
    assert canonical["kind"] == "walkforward"
    assert canonical["walkforward"] == {"train": 252 * form["train_years"], "test": 252, "step": None,
                                        "anchored": False}
    assert canonical["data"]["start"] == form["start"] and canonical["data"]["symbols"] == sorted(form["symbols"])
    assert canonical["costs"] == (COSTS if form["costs"] else {})
    assert canonical["analytics"]["benchmark"] in form["symbols"]


def test_the_grid_keeps_the_allowed_order_whatever_order_values_were_chosen() -> None:
    a = build_config({**VALID, "params": {**VALID["params"], "lookback": [120, 60]}}, CATALOG)
    b = build_config({**VALID, "params": {**VALID["params"], "lookback": [60, 120]}}, CATALOG)
    assert a == b and a["strategy"]["grid"]["lookback"] == [60, 120]


def test_real_data_points_at_the_dataset_file_and_the_risk_free_rate() -> None:
    cfg = build_config(VALID, CATALOG)
    assert cfg["data"]["bars"] == "data/etf.parquet" and cfg["data"]["rates"] == "data/rates.parquet"
    syn = build_config(next(c["form"] for c in CASES if c["name"].startswith("valid synthetic")), CATALOG)
    assert "synthetic_bars" in syn["data"] and "rates" not in syn["data"]


def test_an_invalid_form_raises_with_every_error() -> None:
    with pytest.raises(FormInvalid) as err:
        build_config({**VALID, "strategy": "magic", "costs": None}, CATALOG)
    assert {e.field for e in err.value.errors} == {"strategy", "costs"}


def test_lookback_days() -> None:
    assert lookback_days("tsmom", {"lookback": [60, 250], "vol_window": [20, 60]}) == 310
    assert lookback_days("xsmom", {"lookback_months": [6, 12], "skip_months": [0, 1]}) == 13 * 21
