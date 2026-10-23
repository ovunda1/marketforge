"""Date range and universe filters on bar runs (M11): `[data] start`, `end`, `symbols`."""

import copy
import json

import pytest

from research import runs
from research.data import store
from research.sim.bars import generate_bars

BARS_DATA = {"synthetic_bars": {"seed": 4, "symbols": ["A", "B", "C", "D", "E"], "n_days": 600,
                                "start": "2015-01-02"}}
WALKFORWARD = {
    "kind": "walkforward", "seed": 0, "data": BARS_DATA,
    "execution": {"capital": 1_000_000_000_000},
    "strategy": {"name": "tsmom", "grid": {"lookback": [20], "vol_window": [20], "target_vol": [0.1],
                                           "max_leverage": [2.0]}},
    "walkforward": {"train": 150, "test": 100},
}


def filtered(**data) -> dict:
    cfg = copy.deepcopy(WALKFORWARD)
    cfg["data"].update(data)
    return cfg


def test_the_filters_keep_only_the_range_and_symbols_and_are_recorded(tmp_path) -> None:
    cfg = filtered(start="2015-06-01", end="2016-12-30", symbols=["B", "D"])
    record = runs.run(cfg, out_dir=tmp_path)
    results = json.loads((record.path.parent / "results.json").read_text())
    metrics = json.loads((record.path.parent / "metrics.json").read_text())
    assert results["symbols"] == ["B", "D"]
    assert "2015-06-01" <= metrics["period"]["start"] and metrics["period"]["end"] <= "2016-12-30"
    entry = record.data["data"][0]
    assert entry["filter"] == {"start": "2015-06-01", "end": "2016-12-30", "symbols": ["B", "D"]}
    df = generate_bars(**BARS_DATA["synthetic_bars"])
    assert entry["sha256"] == store.content_hash(df, "bars", {"source": "synthetic", "generator": "research.sim.bars",
                                                              "params": BARS_DATA["synthetic_bars"]})  # the whole dataset


def test_a_filtered_run_reproduces(tmp_path) -> None:
    record = runs.run(filtered(start="2015-03-02", symbols=["A", "C", "E"]), out_dir=tmp_path / "runs")
    rep = runs.reproduce(record.path, work_dir=tmp_path / "again")
    assert rep.identical, rep


def test_without_filters_nothing_changes(tmp_path) -> None:
    a = runs.run(WALKFORWARD, out_dir=tmp_path / "a")
    assert "filter" not in a.data["data"][0]
    b = runs.run(filtered(start="2000-01-03", end="2030-01-02", symbols=["A", "B", "C", "D", "E"]), out_dir=tmp_path / "b")
    assert (a.path.parent / "results.json").read_bytes() == (b.path.parent / "results.json").read_bytes()


@pytest.mark.parametrize("data,match", [
    ({"start": "2015/01/01"}, "start must be a date"),
    ({"end": 20150101}, "end must be a date"),
    ({"start": "2016-01-01", "end": "2015-01-01"}, "start is after end"),
    ({"symbols": []}, "non-empty list"),
    ({"symbols": ["A", "A"]}, "distinct"),
    ({"colour": "red"}, "either synthetic_bars"),
])
def test_bad_filters_are_config_errors(data, match) -> None:
    with pytest.raises(runs.ConfigError, match=match):
        runs.validate_config(filtered(**data))


@pytest.mark.parametrize("data,match", [
    ({"symbols": ["A", "ZZZ"]}, "not in the data: ZZZ"),
    ({"start": "2030-01-02"}, "no bars left"),
])
def test_filters_that_select_nothing_fail_the_run(data, match, tmp_path) -> None:
    with pytest.raises(runs.ConfigError, match=match):
        runs.run(filtered(**data), out_dir=tmp_path)
