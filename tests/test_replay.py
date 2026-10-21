"""mf-journal and mf-replay end to end (M4): determinism across processes, equality with
the M3 driver and reference output, snapshot + tail, and damaged journals."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from reference.generator import generate
from reference.run import run_lines

DIFFTEST_SEED = 20261020
JOURNAL_HEADER_SIZE = 16


def write_commands(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def make_journal(tools: dict[str, Path], cmds: Path, journal: Path) -> Path:
    result = subprocess.run(
        [str(tools["mf-journal"]), str(cmds), str(journal)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return journal


def replay(tools: dict[str, Path], *args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(tools["mf-replay"]), *map(str, args)], capture_output=True, text=True
    )


def replay_hash(tools: dict[str, Path], *args: str | Path) -> str:
    result = replay(tools, *args)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("sha256="), result.stdout
    return lines[0].removeprefix("sha256=")


def sha256_of_lines(lines: list[str]) -> str:
    return hashlib.sha256("".join(line + "\n" for line in lines).encode()).hexdigest()


@pytest.fixture(scope="module")
def difftest_journal(tools: dict[str, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The CI difftest's 100k commands, as text and as a journal."""
    tmp = tmp_path_factory.mktemp("difftest")
    cmds = write_commands(tmp / "difftest.cmds", generate(DIFFTEST_SEED, 100_000))
    return cmds, make_journal(tools, cmds, tmp / "difftest.mfj")


def test_same_journal_gives_the_same_hash_in_fresh_processes(
    tools: dict[str, Path], difftest_journal: tuple[Path, Path]
) -> None:
    _, journal = difftest_journal
    assert replay_hash(tools, journal) == replay_hash(tools, journal)


def test_journal_of_difftest_commands_reproduces_the_driver_output(
    tools: dict[str, Path], driver: Path, difftest_journal: tuple[Path, Path], tmp_path: Path
) -> None:
    cmds, journal = difftest_journal
    driver_out = tmp_path / "driver.events"
    subprocess.run([str(driver), str(cmds), str(driver_out)], check=True)
    replay_out = tmp_path / "replay.events"
    digest = replay_hash(tools, journal, "--events", replay_out)
    assert replay_out.read_bytes() == driver_out.read_bytes()
    assert digest == hashlib.sha256(driver_out.read_bytes()).hexdigest()


def test_replay_hash_matches_the_python_reference(tools: dict[str, Path], tmp_path: Path) -> None:
    # Cross-language: hashlib over the reference engine's stream. (20k commands to keep
    # the slow reference cheap; the 100k driver equality is covered above and by the
    # difftest.)
    lines = generate(DIFFTEST_SEED + 1, 20_000)
    journal = make_journal(tools, write_commands(tmp_path / "c.cmds", lines), tmp_path / "c.mfj")
    assert replay_hash(tools, journal) == sha256_of_lines(list(run_lines(lines)))


def test_snapshot_plus_tail_equals_full_replay(tools: dict[str, Path], tmp_path: Path) -> None:
    lines = generate(7, 10_000)
    journal = make_journal(tools, write_commands(tmp_path / "c.cmds", lines), tmp_path / "c.mfj")
    full_out = tmp_path / "full.events"
    snaps = tmp_path / "snaps"
    replay_hash(tools, journal, "--events", full_out, "--snapshot-every", "2500", "--snapshot-dir", snaps)
    written = sorted(snaps.glob("snap-*.mfsnap"))
    assert [p.name for p in written] == [f"snap-{n:012d}.mfsnap" for n in (2500, 5000, 7500, 10000)]

    full = full_out.read_text()
    for snap in written:
        seq = int(snap.stem.removeprefix("snap-"))
        marker = f"BOOK seq={seq} "
        suffix = full[full.index("\n", full.index(marker)) + 1 :]
        tail_out = tmp_path / f"tail-{seq}.events"
        digest = replay_hash(tools, journal, "--snapshot", snap, "--events", tail_out)
        assert tail_out.read_text() == suffix, snap.name
        assert digest == hashlib.sha256(suffix.encode()).hexdigest(), snap.name


def test_truncated_journal_is_rejected_with_no_hash(tools: dict[str, Path], tmp_path: Path) -> None:
    journal = make_journal(tools, write_commands(tmp_path / "c.cmds", generate(3, 1000)), tmp_path / "c.mfj")
    data = journal.read_bytes()
    bad = tmp_path / "truncated.mfj"
    bad.write_bytes(data[:-5])
    result = replay(tools, bad)
    assert result.returncode == 4
    assert result.stdout == ""
    assert "truncated" in result.stderr and "truncated.mfj" in result.stderr
    assert "last good seq_no 999" in result.stderr


def test_corrupt_journal_is_rejected_with_no_hash(tools: dict[str, Path], tmp_path: Path) -> None:
    journal = make_journal(tools, write_commands(tmp_path / "c.cmds", generate(3, 1000)), tmp_path / "c.mfj")
    data = bytearray(journal.read_bytes())
    data[len(data) // 2] ^= 0x01
    bad = tmp_path / "corrupt.mfj"
    bad.write_bytes(bytes(data))
    result = replay(tools, bad)
    assert result.returncode == 4
    assert result.stdout == ""
    assert "byte offset" in result.stderr and "last good seq_no" in result.stderr


def test_bad_snapshot_is_rejected(tools: dict[str, Path], tmp_path: Path) -> None:
    journal = make_journal(tools, write_commands(tmp_path / "c.cmds", generate(3, 100)), tmp_path / "c.mfj")
    junk = tmp_path / "junk.mfsnap"
    junk.write_bytes(b"MFSNAPSH junk")
    result = replay(tools, journal, "--snapshot", junk)
    assert result.returncode == 5
    assert result.stdout == ""


def test_mf_journal_refuses_to_overwrite_and_to_renumber(tools: dict[str, Path], tmp_path: Path) -> None:
    cmds = write_commands(tmp_path / "c.cmds", ["N 1 1 1 B LMT GTC 0 CN 100 5"])
    existing = tmp_path / "existing.mfj"
    existing.write_bytes(b"keep")
    result = subprocess.run([str(tools["mf-journal"]), str(cmds), str(existing)], capture_output=True, text=True)
    assert result.returncode != 0 and existing.read_bytes() == b"keep"

    # The journal assigns seq_nos 1, 2, 3...; input that disagrees would replay to a
    # different stream than the text file, so it is refused.
    gappy = write_commands(tmp_path / "gap.cmds", ["N 1 1 1 B LMT GTC 0 CN 100 5", "C 3 1"])
    result = subprocess.run(
        [str(tools["mf-journal"]), str(gappy), str(tmp_path / "gap.mfj")], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "seq_no 3" in result.stderr


def test_usage_errors(tools: dict[str, Path]) -> None:
    assert replay(tools).returncode == 1
    assert replay(tools, "/nonexistent/journal.mfj").returncode == 1  # I/O
