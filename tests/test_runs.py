"""Run records (M8): every run stores its config, data checksums, seed, engine and git
commit, and a stored run reproduces byte-identical results."""

import json
import os
import subprocess
import sys
import tomllib

import mfengine
import pytest

from conftest import REPO_ROOT
from research import runs
from research.data import store
from research.sim.bars import generate_bars

EXAMPLES = REPO_ROOT / "research" / "examples"

EVENT = {
    "kind": "event", "seed": 3,
    "data": {"synthetic": {"seed": 21, "duration_ns": 5_000_000_000}},
    "broker": {"latency_ns": 1_000_000, "jitter_ns": 200_000},
    "strategy": {"name": "obmom", "params": {"horizon_ns": 200_000_000, "threshold": 2.0, "qty": 2,
                                             "max_position": 6}},
}

BARS_DATA = {"synthetic_bars": {"seed": 4, "symbols": ["A", "B", "C", "D", "E"], "n_days": 500,
                                "start": "2015-01-02"}}

WALKFORWARD = {
    "kind": "walkforward", "seed": 0, "data": BARS_DATA,
    "execution": {"capital": 1_000_000_000_000},
    "strategy": {"name": "tsmom", "grid": {"lookback": [20, 60], "vol_window": [20], "target_vol": [0.1],
                                           "max_leverage": [2.0]}},
    "walkforward": {"train": 200, "test": 100},
}

BARS = {
    "kind": "bars", "seed": 0, "data": BARS_DATA,
    "execution": {"capital": 1_000_000_000_000, "lag": 1, "price": "open"},
    "strategy": {"name": "xsmom", "params": {"lookback_months": 6, "skip_months": 1, "quantile": 0.4}},
}


@pytest.mark.parametrize("config", [EVENT, WALKFORWARD, BARS], ids=["event", "walkforward", "bars"])
def test_a_stored_run_reproduces_byte_identical_results(config, tmp_path) -> None:
    record = runs.run(config, out_dir=tmp_path / "runs")
    rec_path = record.path
    stored = (rec_path.parent / "results.json").read_bytes()
    rep = runs.reproduce(rec_path, work_dir=tmp_path / "again")
    assert rep.identical, rep
    assert (rep.results_path.read_bytes()) == stored
    assert rep.results_sha256 == record.data["results_sha256"]


def test_record_contents(tmp_path) -> None:
    record = runs.run(EVENT, out_dir=tmp_path).data
    assert record["kind"] == "event" and record["seed"] == 3
    assert record["config"] == runs.canonical_config(EVENT)
    assert [d["name"] for d in record["data"]] == ["quotes", "trades"]
    for d in record["data"]:
        assert len(d["sha256"]) == 64 and d["rows"] > 0 and d["generated"]["seed"] == 21
    assert record["engine"] == {"version": mfengine.version(), "build_type": mfengine.BUILD_TYPE,
                                "invariant_checks": mfengine.INVARIANT_CHECKS}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    assert record["git"]["commit"] == head and isinstance(record["git"]["dirty"], bool)
    assert set(record["versions"]) >= {"python", "numpy", "pandas", "pyarrow"}
    assert record["platform"]["machine"]


def test_conservative_defaults_are_recorded(tmp_path) -> None:
    a = runs.run(EVENT, out_dir=tmp_path).data["assumptions"]
    assert a["queue"] == "pessimistic" and a["consumed_liquidity"] == "stays_removed"
    opt_in = {**EVENT, "replay": {"queue": "optimistic", "consumed_liquidity": "restore"}}
    b = runs.run(opt_in, out_dir=tmp_path).data["assumptions"]
    assert b["queue"] == "optimistic" and b["consumed_liquidity"] == "restore"


def test_bar_assumptions_are_recorded(tmp_path) -> None:
    a = runs.run(BARS, out_dir=tmp_path).data["assumptions"]
    assert a["fill_lag"] == 1 and a["fill_price"] == "open"
    assert a["costs"] == {} and a["survivorship"]  # no [costs]: gross only


def test_seed_changes_the_result(tmp_path) -> None:
    a = runs.run(EVENT, out_dir=tmp_path).data["results_sha256"]
    b = runs.run({**EVENT, "seed": 4}, out_dir=tmp_path).data["results_sha256"]
    assert a != b


def test_changed_data_refuses_to_reproduce(tmp_path) -> None:
    path = tmp_path / "bars.parquet"
    df = generate_bars(seed=4, symbols=("A", "B", "C"), n_days=300, start="2015-01-02")
    store.write(df, path, "bars", {"source": "test", "survivorship": "synthetic"})
    config = {**BARS, "data": {"bars": str(path)}}
    record = runs.run(config, out_dir=tmp_path / "runs")
    df.loc[5, "volume"] += 1
    store.write(df, path, "bars", {"source": "test", "survivorship": "synthetic"})
    with pytest.raises(runs.DataMismatch, match="bars"):
        runs.reproduce(record.path, work_dir=tmp_path / "again")


def test_a_different_engine_refuses_to_reproduce(tmp_path) -> None:
    record = runs.run(BARS, out_dir=tmp_path)
    data = json.loads(record.path.read_text())
    data["engine"]["version"] = "9.9.9"
    record.path.write_text(json.dumps(data))
    with pytest.raises(runs.EngineMismatch):
        runs.reproduce(record.path, work_dir=tmp_path / "again")


@pytest.mark.parametrize("change", [
    {"broker": {"latency": 5}},
    {"replay": {"queue": "fifo"}},
    {"replay": {"consumed_liquidity": "sometimes"}},
    {"costs": {"model": "spread"}},
    {"strategy": {"name": "nope"}},
    {"oops": 1},
])
def test_configs_are_strict(change, tmp_path) -> None:
    with pytest.raises(runs.ConfigError):
        runs.run({**EVENT, **change}, out_dir=tmp_path)


def test_same_bar_execution_is_a_config_error(tmp_path) -> None:
    with pytest.raises(runs.ConfigError, match="lag"):
        runs.run({**BARS, "execution": {**BARS["execution"], "lag": 0}}, out_dir=tmp_path)


def test_records_never_hold_the_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "supersecret-123")
    record = runs.run(BARS, out_dir=tmp_path)
    for path in record.path.parent.iterdir():
        assert b"supersecret-123" not in path.read_bytes()


# --- Committed examples -----------------------------------------------------------------------

def test_example_configs_are_valid() -> None:
    configs = sorted(EXAMPLES.glob("*.toml"))
    assert {p.stem for p in configs} >= {"obmom_synthetic", "tsmom_walkforward", "xsmom_walkforward"}
    for path in configs:
        runs.validate_config(tomllib.loads(path.read_text()))


@pytest.mark.parametrize("record", sorted((EXAMPLES / "runs").glob("*/record.json")),
                         ids=lambda p: p.parent.name)
def test_committed_example_runs_reproduce(record, tmp_path) -> None:
    rep = runs.reproduce(record, work_dir=tmp_path)
    assert rep.identical, f"{record.parent.name}: results changed ({rep.warnings})"


def test_there_are_committed_example_runs() -> None:
    assert len(list((EXAMPLES / "runs").glob("*/record.json"))) >= 3


# --- CLI -------------------------------------------------------------------------------------

def test_cli_run_and_reproduce(tmp_path) -> None:
    cfg = tmp_path / "bars.toml"
    cfg.write_text(runs.to_toml(BARS))
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    out = subprocess.run([sys.executable, "-m", "research", "run", str(cfg), "--out", str(tmp_path / "runs")],
                         cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    record = next((tmp_path / "runs").glob("*/record.json"))
    assert str(record) in out.stdout
    if mfengine.INVARIANT_CHECKS:
        assert "invariant checks are on" in out.stderr
    rep = subprocess.run([sys.executable, "-m", "research", "reproduce", str(record), "--work-dir",
                          str(tmp_path / "again")], cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    assert rep.returncode == 0, rep.stderr
    assert "identical" in rep.stdout


# --- Costs and analytics config (M9) ----------------------------------------------------------------

@pytest.mark.parametrize(("change", "match"), [
    ({"costs": {"spread": {"half_spread_bps": 2.0}}}, "fill price"),
    ({"costs": {"impact": {"y": 1.0}}}, "fill price"),
    ({"costs": {"commission": {"per_shar": "0.1"}}}, "per_shar"),
])
def test_event_cost_config_is_checked(change, match, tmp_path) -> None:
    with pytest.raises(runs.ConfigError, match=match):
        runs.run({**EVENT, **change}, out_dir=tmp_path)


@pytest.mark.parametrize("analytics", [
    {"capacity": [10, 5]},
    {"capacity": [0]},
    {"benchmark": 3},
    {"periods_per_year": 0},
    {"nope": 1},
])
def test_analytics_config_is_checked(analytics, tmp_path) -> None:
    with pytest.raises(runs.ConfigError, match="analytics"):
        runs.run({**WALKFORWARD, "analytics": analytics}, out_dir=tmp_path)


def test_an_unknown_benchmark_is_an_error(tmp_path) -> None:
    with pytest.raises(runs.ConfigError, match="benchmark"):
        runs.run({**WALKFORWARD, "analytics": {"benchmark": "ZZZ"}}, out_dir=tmp_path)


def test_rates_are_only_for_bar_runs(tmp_path) -> None:
    with pytest.raises(runs.ConfigError, match="rates"):
        runs.run({**EVENT, "data": {**EVENT["data"], "rates": "x.parquet"}}, out_dir=tmp_path)


def test_costs_and_trials_are_recorded(tmp_path) -> None:
    cfg = {**WALKFORWARD, "costs": {"spread": {"half_spread_bps": 2.0}, "sec_fee": {"schedule": "sec31"}}}
    a = runs.run(cfg, out_dir=tmp_path).data["assumptions"]
    assert a["costs"]["spread"] == {"half_spread_bps": 2.0}
    assert a["costs"]["sec_fee"]["schedule"][0]["rate_per_million"]
    assert a["liquidate_at_window_end"] is True
    assert a["trials"] == {"grid_size": 2, "folds": a["folds"], "backtests": 2 * a["folds"] + a["folds"],
                           "dsr_trials": 2, "capacity_points": 0}


def test_example_runs_carry_tearsheets() -> None:
    for record in sorted((EXAMPLES / "runs").glob("*/record.json")):
        assert (record.parent / "metrics.json").is_file() and (record.parent / "tearsheet.html").is_file()
