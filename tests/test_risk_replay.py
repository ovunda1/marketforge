"""Risk gate end to end through the CLI tools (M5): a journal with limits, admin
commands and a moving clock replays to the same hash in fresh processes, equal to the
driver's output, from the start or a snapshot; YAML config gives the same stream as the
equivalent admin commands; and the risk golden scenarios match through mf_driver."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from reference.difftest import run_driver
from risk_flow import HEADER, generate

RISK_GOLDEN_DIR = REPO_ROOT / "engine" / "tests" / "golden" / "risk"
FIXTURES = REPO_ROOT / "engine" / "tests" / "journal"
SEED = 20261021
N = 20_000

# The YAML equivalent of risk_flow.HEADER: parsed, it gives the same three commands.
CONFIG = """\
instrument: {max_qty: 40, price_band_bps: 300, reference_price: 100}
accounts:
  1: {max_notional: 1500, position_limit: 30, credit: 3000, max_open_orders: 2,
      rate: {per_sec: 5, burst: 2}}
  default: {max_notional: 3000, position_limit: 60, credit: 6000, max_open_orders: 3,
            rate: {per_sec: 20, burst: 5}}
"""

RISK_REJECTS = (
    "MARKET_CLOSED", "KILL_SWITCH", "RATE_LIMIT", "MAX_QTY", "PRICE_BAND", "MAX_NOTIONAL",
    "MAX_OPEN_ORDERS", "POSITION_LIMIT", "INSUFFICIENT_CREDIT", "DUPLICATE_ORDER_ID",
)


def run_tool(path: Path, *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(path), *map(str, args)], capture_output=True, text=True)


def replay_hash(tools: dict[str, Path], *args: str | Path) -> str:
    result = run_tool(tools["mf-replay"], *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().removeprefix("sha256=")


def journal(tools: dict[str, Path], cmds: Path, out: Path, *extra: str | Path) -> Path:
    result = run_tool(tools["mf-journal"], cmds, out, *extra)
    assert result.returncode == 0, result.stderr
    return out


@pytest.fixture(scope="module")
def flow(tools: dict[str, Path], driver: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("risk")
    cmds = tmp / "flow.cmds"
    cmds.write_text("\n".join(generate(SEED, N)) + "\n")
    events = tmp / "driver.events"
    subprocess.run([str(driver), str(cmds), str(events)], check=True)
    return {"cmds": cmds, "journal": journal(tools, cmds, tmp / "flow.mfj"), "events": events}


def test_replay_in_fresh_processes_gives_the_driver_hash(
    tools: dict[str, Path], flow: dict[str, Path]
) -> None:
    first = replay_hash(tools, flow["journal"])
    second = replay_hash(tools, flow["journal"])
    assert first == second
    assert first == hashlib.sha256(flow["events"].read_bytes()).hexdigest()


def test_stream_reproduces_every_risk_reject(flow: dict[str, Path]) -> None:
    text = flow["events"].read_text()
    missing = [code for code in RISK_REJECTS if f"REJ seq=" not in text or f"reason={code}\n" not in text]
    assert not missing
    for line in ("CXL", "SES", "ADM", "AREJ"):
        assert f"\n{line} seq=" in text, line


def test_snapshot_plus_tail_equals_full_replay(
    tools: dict[str, Path], flow: dict[str, Path], tmp_path: Path
) -> None:
    full = tmp_path / "full.events"
    snaps = tmp_path / "snaps"
    replay_hash(tools, flow["journal"], "--events", full, "--snapshot-every", "5000",
                "--snapshot-dir", snaps)
    text = full.read_text()
    written = sorted(snaps.glob("snap-*.mfsnap"))
    assert len(written) == 4
    for snap in written:
        seq = int(snap.stem.removeprefix("snap-"))
        marker = f"BOOK seq={seq} "
        suffix = text[text.index("\n", text.index(marker)) + 1 :]
        assert replay_hash(tools, flow["journal"], "--snapshot", snap) == hashlib.sha256(
            suffix.encode()
        ).hexdigest(), snap.name


def test_yaml_config_gives_the_same_stream_as_the_admin_commands(
    tools: dict[str, Path], driver: Path, flow: dict[str, Path], tmp_path: Path
) -> None:
    config = tmp_path / "risk.yaml"
    config.write_text(CONFIG)
    lines = generate(SEED, N)
    assert lines[1 : 1 + len(HEADER)] == HEADER
    body = tmp_path / "body.cmds"  # seq_nos continue at len(HEADER) + 1
    body.write_text("\n".join(lines[1 + len(HEADER) :]) + "\n")

    events = tmp_path / "config.events"
    result = run_tool(driver, "--config", config, body, events)
    assert result.returncode == 0, result.stderr
    assert events.read_bytes() == flow["events"].read_bytes()

    journaled = journal(tools, body, tmp_path / "config.mfj", "--config", config)
    assert replay_hash(tools, journaled) == hashlib.sha256(events.read_bytes()).hexdigest()


def test_bad_config_is_refused(tools: dict[str, Path], driver: Path, tmp_path: Path) -> None:
    config = tmp_path / "bad.yaml"
    config.write_text("instrument: {max_qty: -1}\n")
    cmds = tmp_path / "c.cmds"
    cmds.write_text("C 1 1\n")
    result = run_tool(driver, "--config", config, cmds)
    assert result.returncode == 2 and "max_qty" in result.stderr
    result = run_tool(tools["mf-journal"], cmds, tmp_path / "c.mfj", "--config", config)
    assert result.returncode == 2 and "max_qty" in result.stderr
    assert not (tmp_path / "c.mfj").exists()


def test_committed_v2_journal_replays_to_its_recorded_hash(tools: dict[str, Path]) -> None:
    recorded = (FIXTURES / "v2.sha256").read_text().strip()
    assert replay_hash(tools, FIXTURES / "v2.mfj") == recorded


def test_mf_journal_refuses_time_going_backwards(tools: dict[str, Path], tmp_path: Path) -> None:
    cmds = tmp_path / "c.cmds"
    cmds.write_text("T 10\nC 1 1\nT 9\nC 2 1\n")
    result = run_tool(tools["mf-journal"], cmds, tmp_path / "c.mfj")
    assert result.returncode == 2 and "line 3" in result.stderr
    assert not (tmp_path / "c.mfj").exists()


RISK_SCENARIOS = sorted(p.stem for p in RISK_GOLDEN_DIR.glob("*.cmds"))


def test_risk_scenarios_are_discovered() -> None:
    assert len(RISK_SCENARIOS) >= 7


@pytest.mark.parametrize("name", RISK_SCENARIOS)
def test_driver_matches_risk_golden(name: str, driver: Path) -> None:
    expected = (RISK_GOLDEN_DIR / f"{name}.events").read_text().splitlines()
    assert run_driver(driver, RISK_GOLDEN_DIR / f"{name}.cmds") == expected
