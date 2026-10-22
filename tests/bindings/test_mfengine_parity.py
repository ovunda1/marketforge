"""The key M7 test: commands driven through mfengine give the same canonical event stream,
byte for byte and so the same SHA-256, as mf_driver on the same commands."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import mfengine as mf
from conftest import GOLDEN_DIR, REPO_ROOT
from mf_helpers import batch_of, built_commands, drive, run_driver, timed, write_lines
from reference.generator import generate
from risk_flow import HEADER
from risk_flow import generate as risk_generate

RISK_GOLDEN_DIR = REPO_ROOT / "engine" / "tests" / "golden" / "risk"
DIFFTEST_SEED = 20261020
RISK_SEED = 20261021

# The YAML equivalent of risk_flow.HEADER (as in tests/test_risk_replay.py).
CONFIG = """\
instrument: {max_qty: 40, price_band_bps: 300, reference_price: 100}
accounts:
  1: {max_notional: 1500, position_limit: 30, credit: 3000, max_open_orders: 2,
      rate: {per_sec: 5, burst: 2}}
  default: {max_notional: 3000, position_limit: 60, credit: 6000, max_open_orders: 3,
            rate: {per_sec: 20, burst: 5}}
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("profile", ["narrow", "wide"])
def test_python_built_commands_match_the_driver(profile: str, driver: Path, tmp_path: Path) -> None:
    lines = generate(DIFFTEST_SEED, 100_000, profile)
    cmds = write_lines(tmp_path / "flow.cmds", lines)
    expected = run_driver(driver, cmds)
    got = drive((c, 0) for c in built_commands(lines))
    assert sha(got) == sha(expected)
    assert got == expected


def test_risk_flow_with_clock_lines_matches_the_driver(driver: Path, tmp_path: Path) -> None:
    lines = risk_generate(RISK_SEED, 20_000)
    expected = run_driver(driver, write_lines(tmp_path / "risk.cmds", lines))
    got = drive(timed(lines))
    assert sha(got) == sha(expected)


GOLDEN = sorted([*GOLDEN_DIR.glob("*.cmds"), *RISK_GOLDEN_DIR.glob("*.cmds")])


@pytest.mark.parametrize("cmds", GOLDEN, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_golden_scenarios_match_their_expected_output(cmds: Path) -> None:
    expected = cmds.with_suffix(".events").read_bytes()
    assert drive(timed(cmds.read_text().splitlines())) == expected


def test_yaml_config_through_python_matches_driver_with_config(driver: Path, tmp_path: Path) -> None:
    lines = risk_generate(RISK_SEED, 5_000)
    assert lines[1 : 1 + len(HEADER)] == HEADER
    body = lines[1 + len(HEADER) :]  # seq_nos continue at len(HEADER) + 1
    config = tmp_path / "risk.yaml"
    config.write_text(CONFIG)
    expected = run_driver(driver, write_lines(tmp_path / "body.cmds", body), config)

    prefix = [(c.with_seq_no(i + 1), 0) for i, c in enumerate(mf.load_risk_config(config))]
    assert sha(drive([*prefix, *timed(body)])) == sha(expected)


def test_batch_gives_the_same_events_as_one_call_per_command() -> None:
    commands = timed(risk_generate(RISK_SEED, 20_000))
    one_by_one = mf.Engine()
    singles = [one_by_one.process(c, timestamp=ts) for c, ts in commands]

    batched = mf.Engine()
    result = batched.process_batch(batch_of(commands))
    assert result.command_count == len(commands)
    assert [result.events_of(k) for k in range(len(commands))] == singles
    assert list(result) == [e for events in singles for e in events]
    assert mf.format_depth(batched) == mf.format_depth(one_by_one)
    assert batched.positions() == one_by_one.positions()


def test_batches_in_chunks_give_the_driver_stream(driver: Path, tmp_path: Path) -> None:
    # A backtest pattern: background flow in batches, reading the book between them.
    # Events come from the batches; the BOOK line is checked at each chunk boundary.
    lines = generate(DIFFTEST_SEED, 30_000)
    expected = run_driver(driver, write_lines(tmp_path / "flow.cmds", lines)).decode().splitlines()
    book_lines = {int(ln.split()[1].removeprefix("seq=")): ln for ln in expected if ln.startswith("BOOK")}
    commands = built_commands(lines)
    engine = mf.Engine()
    events: list[str] = []
    for start in range(0, len(commands), 1_000):
        chunk = commands[start : start + 1_000]
        result = engine.process_batch(batch_of((c, 0) for c in chunk))
        events += [str(e) for e in result]
        last = chunk[-1].seq_no
        assert mf.format_top_of_book(engine, last) == book_lines[last]
    assert events == [ln for ln in expected if not ln.startswith(("BOOK", "LVL"))]
    assert mf.format_depth(engine).splitlines() == [ln for ln in expected if ln.startswith("LVL")]
