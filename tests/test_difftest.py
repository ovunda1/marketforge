"""Differential test: the C++ engine and the Python reference must agree line for line."""

import sys
from pathlib import Path

import pytest

from conftest import REGRESSIONS_DIR
from reference.difftest import compare_file, difftest, first_mismatch, save_regression
from reference.generator import generate
from reference.run import run_lines

CI_SEED = 20261020
CI_COMMANDS = 100_000


def test_first_mismatch_reports_line_seq_and_command() -> None:
    commands = ["# header", "N 1 1 1 B LMT GTC 0 CN 100 5", "C 2 1"]
    expected = ["ACK seq=1 id=1 x", "BOOK seq=1 a", "CXL seq=2 id=1 qty=5 reason=USER", "BOOK seq=2 b"]
    actual = ["ACK seq=1 id=1 x", "BOOK seq=1 a", "REJ seq=2 id=1 reason=UNKNOWN_ORDER", "BOOK seq=2 b"]
    m = first_mismatch(expected, actual, commands)
    assert m is not None
    assert (m.output_line, m.seq, m.command) == (3, 2, "C 2 1")
    assert m.expected == expected[2] and m.actual == actual[2]
    assert m.context == expected[:2]


def test_first_mismatch_handles_length_difference_and_depth() -> None:
    m = first_mismatch(["BOOK seq=1 a", "LVL side=B px=1 qty=1 orders=1:1"], ["BOOK seq=1 a"], [])
    assert m is not None
    assert (m.output_line, m.seq, m.actual) == (2, None, "<missing>")
    assert first_mismatch(["a"], ["a"], []) is None


def test_save_regression_truncates_after_failing_seq(tmp_path: Path) -> None:
    commands = ["# seed=9 n=4", "C 1 1", "C 2 1", "C 3 1", "C 4 1"]
    path = save_regression(commands, 2, "seed-9", tmp_path)
    assert path == tmp_path / "seed-9.cmds"
    assert path.read_text().splitlines() == ["# seed=9 n=4", "C 1 1", "C 2 1"]
    # No seq (depth mismatch): keep everything.
    assert save_regression(commands, None, "all", tmp_path).read_text().splitlines() == commands


def test_harness_catches_an_injected_engine_bug(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # A fake "engine" that is the reference with one event corrupted.
    fake = tmp_path / "fake_driver.py"
    fake.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n"
        "from reference.run import run_lines\n"
        "out = list(run_lines(open(sys.argv[1]).read().splitlines()))\n"
        "i = next(i for i, l in enumerate(out) if l.startswith('TRD'))\n"
        "out[i] = out[i].replace('qty=', 'qty=9')\n"
        "open(sys.argv[2], 'w').write('\\n'.join(out) + '\\n')\n"
    )
    wrapper = tmp_path / "fake_driver"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {fake} \"$@\"\n")
    wrapper.chmod(0o755)

    regressions = tmp_path / "regressions"
    m = difftest(seed=3, n=500, driver=wrapper, workdir=tmp_path, regressions_dir=regressions)
    assert m is not None and m.expected.startswith("TRD") and m.seq is not None
    report = capsys.readouterr().out
    assert f"seq_no {m.seq}" in report and "seed 3" in report
    saved = regressions / "seed-3.cmds"
    assert saved.exists()
    # The saved file reproduces the failure on its own.
    assert compare_file(saved, wrapper) is not None


def test_difftest_ci(driver: Path, tmp_path: Path) -> None:
    assert difftest(CI_SEED, CI_COMMANDS, driver, tmp_path, REGRESSIONS_DIR) is None


def test_saved_regressions_agree(driver: Path) -> None:
    for path in sorted(REGRESSIONS_DIR.glob("*.cmds")):
        assert compare_file(path, driver) is None, path.name


def test_generated_flow_reaches_every_outcome() -> None:
    """Guards against a generator change quietly making the difftest vacuous."""
    output = "\n".join(run_lines(generate(CI_SEED, 100_000)))
    for token in (
        "reason=INVALID_PRICE", "reason=INVALID_QTY", "reason=INVALID_ORDER_TYPE",
        "reason=DUPLICATE_ORDER_ID", "reason=UNKNOWN_ORDER", "reason=NO_LIQUIDITY",
        "reason=FOK_NOT_FILLABLE", "reason=POST_ONLY_WOULD_CROSS", "reason=REPLACE_QTY_TOO_LOW",
        "reason=USER", "reason=UNFILLED", "reason=SELF_TRADE", "prio=KEPT", "prio=LOST",
        "type=MKT", "tif=FOK", "po=1", "stp=CO", "stp=CB",
    ):
        assert output.count(token) >= 20, token
