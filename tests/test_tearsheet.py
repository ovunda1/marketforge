"""Tearsheets (M9): metrics.json and tearsheet.html per run, gross and net side by side,
portfolio-level only, rendered from the metrics alone and byte-reproducible."""

from __future__ import annotations

import copy
import json

import pytest

from conftest import REPO_ROOT
from research import runs
from research.analytics import tearsheet

COSTS = {"commission": {"per_share": "0.0035"}, "sec_fee": {"schedule": "sec31"},
         "spread": {"half_spread_bps": 2.0}, "impact": {"y": 1.0},
         "financing": {"borrow_bps": 50, "spread_bps": 50}}

WF = {
    "kind": "walkforward", "seed": 0,
    "data": {"synthetic_bars": {"seed": 4, "symbols": ["A", "B", "C", "D", "E"], "n_days": 700,
                                "start": "2015-01-02", "split_every": 300, "dividend_every": 63}},
    "execution": {"capital": 1_000_000_000_000},
    "strategy": {"name": "tsmom", "grid": {"lookback": [20, 60], "vol_window": [20], "target_vol": [0.1],
                                           "max_leverage": [2.0]}},
    "walkforward": {"train": 300, "test": 200},
    "costs": COSTS,
    "analytics": {"benchmark": "A", "capacity": [1_000_000_000_000, 100_000_000_000_000]},
}

EVENT = {
    "kind": "event", "seed": 3,
    "data": {"synthetic": {"seed": 21, "duration_ns": 5_000_000_000}},
    "broker": {"latency_ns": 1_000_000, "jitter_ns": 200_000},
    "strategy": {"name": "obmom", "params": {"horizon_ns": 200_000_000, "threshold": 2.0, "qty": 2,
                                             "max_position": 6}},
    "costs": {"maker_taker": {"maker_per_share": "-0.0020", "taker_per_share": "0.0030"}},
}


@pytest.fixture(scope="module")
def wf_run(tmp_path_factory):
    record = runs.run(WF, out_dir=tmp_path_factory.mktemp("runs"))
    d = record.path.parent
    return record, json.loads((d / "metrics.json").read_text()), (d / "tearsheet.html").read_text()


def test_every_run_writes_metrics_and_a_tearsheet(wf_run) -> None:
    record, metrics, html = wf_run
    d = record.path.parent
    assert set(record.data["outputs"]) == {"results.json", "metrics.json", "tearsheet.html"}
    assert metrics["kind"] == "walkforward" and html.startswith("<!doctype html>")
    assert record.data["outputs"]["metrics.json"] == runs.sha256_file(d / "metrics.json")


def test_performance_is_gross_and_net_side_by_side(wf_run) -> None:
    _, metrics, html = wf_run
    perf = metrics["performance"]
    for name in ("annual_return", "volatility", "sharpe", "sortino", "calmar", "max_drawdown",
                 "max_drawdown_duration", "beta", "alpha_annual", "hit_rate", "avg_win", "avg_loss",
                 "profit_factor", "sharpe_se_iid", "sharpe_se_mertens", "psr", "dsr"):
        assert set(perf[name]) == {"gross", "net"}, name
    assert perf["sharpe"]["net"] < perf["sharpe"]["gross"]
    assert "<th>Gross</th><th>Net</th>" in html


def test_the_books_balance(wf_run) -> None:
    _, metrics, _ = wf_run
    pnl, costs = metrics["pnl"], metrics["costs"]
    assert pnl["gross"] - costs["total"] == pnl["net"]
    assert sum(costs["by_component"].values()) == costs["total"]
    assert sum(sum(y.values()) for y in costs["by_year"].values()) == costs["total"]
    sf = metrics["implementation_shortfall"]
    assert sf["total"] == sf["delay"] + sum(sf["costs"].values())


def test_trials_and_uncertainty_are_reported(wf_run) -> None:
    _, metrics, html = wf_run
    t = metrics["trials"]
    assert t["grid_size"] == 2 and t["folds"] == 2
    assert t["backtests"] == 2 * 2 + 2 and t["dsr_trials"] == 2
    assert t["capacity_points"] == 2
    for text in ("Trials", "Sharpe standard error", "Deflated Sharpe", "half_spread_bps", "27.80"):
        assert text in html, text


def test_risk_free_and_benchmark_are_stated(wf_run) -> None:
    _, metrics, _ = wf_run
    assert metrics["conventions"]["risk_free"]["source"] == "none: 0"
    assert metrics["conventions"]["periods_per_year"] == 252
    assert metrics["conventions"]["std"] == "sample (ddof=1)"
    assert metrics["conventions"]["benchmark"] == "A (total return)"


def test_capacity_curve(wf_run) -> None:
    _, metrics, html = wf_run
    cap = metrics["capacity"]
    assert [c["capital"] for c in cap] == [1_000_000_000_000, 100_000_000_000_000]
    assert cap[1]["annual_cost"] > cap[0]["annual_cost"]
    assert cap[1]["max_participation"] > cap[0]["max_participation"]
    assert "Capacity" in html


def test_the_tearsheet_is_rendered_from_the_metrics_alone(wf_run) -> None:
    _, metrics, html = wf_run
    assert tearsheet.render(copy.deepcopy(metrics)) == html
    assert tearsheet.render(metrics) == tearsheet.render(metrics)


def test_the_tearsheet_is_self_contained(wf_run) -> None:
    _, _, html = wf_run
    assert "<script" not in html and "http://" not in html and "https://" not in html
    assert "<svg" in html


def test_the_run_reproduces_its_metrics_and_tearsheet(wf_run, tmp_path) -> None:
    record, _, _ = wf_run
    rep = runs.reproduce(record.path, work_dir=tmp_path)
    assert rep.identical and rep.outputs_identical == {"results.json": True, "metrics.json": True,
                                                       "tearsheet.html": True}


# --- Portfolio level only --------------------------------------------------------------------------------

SYMBOLS = ["A", "B", "C", "D", "E"]


def test_the_metrics_are_portfolio_level(wf_run) -> None:
    _, metrics, _ = wf_run
    tearsheet.check_portfolio_level(metrics, SYMBOLS)


@pytest.mark.parametrize("leak", [
    {"series": {"A": [1.0] * 300}},
    {"by_symbol": {"B": {"pnl": 1}}},
    {"extra": [0.5] * 300},
    {"series": {"positions": [[1, 2]] * 300}},
])
def test_per_symbol_or_unexpected_series_are_refused(wf_run, leak) -> None:
    _, metrics, _ = wf_run
    bad = copy.deepcopy(metrics)
    for k, v in leak.items():
        bad[k] = {**bad[k], **v} if isinstance(v, dict) and isinstance(bad.get(k), dict) else v
    with pytest.raises(ValueError, match="portfolio"):
        tearsheet.check_portfolio_level(bad, SYMBOLS)


def test_committed_showcase_outputs_are_portfolio_level() -> None:
    showcase = REPO_ROOT / "research" / "showcase"
    found = sorted(showcase.glob("*/metrics.json"))
    assert len(found) == 2, "both showcase runs are committed"
    universe = json.loads((REPO_ROOT / "research/data/manifests/tiingo-etf-daily.json").read_text())["universe"]
    for path in found:
        metrics = json.loads(path.read_text())
        tearsheet.check_portfolio_level(metrics, universe)
        assert (path.parent / "tearsheet.html").read_text() == tearsheet.render(metrics)


# --- Event runs ---------------------------------------------------------------------------------------------

def test_event_tearsheet_has_fees_and_measured_shortfall_but_no_sharpe(tmp_path) -> None:
    record = runs.run(EVENT, out_dir=tmp_path)
    metrics = json.loads((record.path.parent / "metrics.json").read_text())
    assert metrics["kind"] == "event"
    assert metrics["pnl"]["gross"] - metrics["costs"]["total"] == metrics["pnl"]["net"]
    assert set(metrics["costs"]["by_component"]) == {"maker_taker"}
    assert set(metrics["implementation_shortfall"]) >= {"delay", "spread_paid", "fills_without_quotes"}
    assert "sharpe" not in json.dumps(metrics["performance"])
    assert "measured, inside gross" in (record.path.parent / "tearsheet.html").read_text()


def test_showcase_configs_are_valid_and_keep_the_m8_grids() -> None:
    import tomllib

    showcase = REPO_ROOT / "research" / "showcase"
    cfgs = {n: runs.validate_config(tomllib.loads((showcase / f"{n}.toml").read_text())) for n in ("tsmom", "xsmom")}
    assert cfgs["tsmom"]["strategy"]["grid"] == {"lookback": [60, 120, 250], "vol_window": [20, 60],
                                                 "target_vol": [0.10], "max_leverage": [2.0]}
    assert cfgs["xsmom"]["strategy"]["grid"] == {"lookback_months": [6, 12], "skip_months": [1],
                                                 "quantile": [0.2, 0.3]}
    for c in cfgs.values():
        assert c["data"]["rates"].endswith("fred-dtb3/rates.parquet")
        assert c["walkforward"] == {"train": 756, "test": 252, "step": None, "anchored": False}
        assert set(c["costs"]) >= {"commission", "sec_fee", "spread", "impact", "financing"}
