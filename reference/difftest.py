"""Differential test harness: C++ engine (mf_driver) vs the Python reference.

The generator writes the command file once; both engines consume that same file and
their canonical outputs are compared line by line, streaming, so 1M commands do not
need both outputs in memory. On the first mismatch it prints the seed, the seq_no, the
command and the reference context, and saves the commands up to that seq_no as a
regression case.

Usage:
  python -m reference.difftest --seed S --n N --driver build/engine/tools/mf_driver
  python -m reference.difftest --replay --driver build/engine/tools/mf_driver
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

from reference.commands import parse_command
from reference.generator import PROFILES, generate
from reference.run import run_lines

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGRESSIONS_DIR = REPO_ROOT / "tests" / "difftest" / "regressions"
MISSING = "<missing>"
CONTEXT_LINES = 5

_SEQ = re.compile(r"\bseq=(\d+)\b")


@dataclass
class Mismatch:
    output_line: int  # 1-based line in the event stream
    seq: int | None  # seq_no of the differing line; None in the final depth
    expected: str  # reference output
    actual: str  # engine output
    command: str | None  # the command that produced the line
    context: list[str]  # reference lines just before the mismatch


class DriverError(RuntimeError):
    pass


def _seq_of(line: str) -> int | None:
    m = _SEQ.search(line)
    return int(m.group(1)) if m else None


def _command_for(seq: int | None, commands: list[str]) -> str | None:
    if seq is None:
        return None
    for line in commands:
        command = parse_command(line)
        if command is not None and command.seq == seq:
            return line
    return None


def _compare(
    expected: Iterable[str], actual: Iterable[str], commands: list[str]
) -> tuple[Mismatch | None, int]:
    """First mismatch (if any) and the number of lines compared."""
    context: deque[str] = deque(maxlen=CONTEXT_LINES)
    n = 0
    for n, (e, a) in enumerate(zip_longest(expected, actual, fillvalue=MISSING), start=1):
        if e != a:
            seq = _seq_of(e) if e != MISSING else None
            if seq is None and a != MISSING:
                seq = _seq_of(a)
            return Mismatch(n, seq, e, a, _command_for(seq, commands), list(context)), n
        context.append(e)
    return None, n


def first_mismatch(
    expected: Iterable[str], actual: Iterable[str], commands: list[str]
) -> Mismatch | None:
    return _compare(expected, actual, commands)[0]


def save_regression(commands: list[str], seq: int | None, name: str, directory: Path) -> Path:
    """Saves the commands up to and including `seq` (all of them if None)."""
    kept = []
    for line in commands:
        command = parse_command(line)
        if command is not None and seq is not None and command.seq > seq:
            break
        kept.append(line)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.cmds"
    path.write_text("\n".join(kept) + "\n")
    return path


def _run_driver_to(driver: Path, cmds_path: Path, events_path: Path) -> None:
    result = subprocess.run(
        [str(driver), str(cmds_path), str(events_path)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise DriverError(f"{driver} exited {result.returncode}: {result.stderr.strip()}")


def run_driver(driver: Path, cmds_path: Path) -> list[str]:
    """The engine's full output for a (small) command file."""
    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "engine.events"
        _run_driver_to(driver, cmds_path, events_path)
        return events_path.read_text().splitlines()


def _lines(path: Path) -> Iterator[str]:
    with path.open() as f:
        for line in f:
            yield line.rstrip("\n")


def compare_file(cmds_path: Path, driver: Path) -> Mismatch | None:
    commands = cmds_path.read_text().splitlines()
    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "engine.events"
        _run_driver_to(driver, cmds_path, events_path)
        return first_mismatch(run_lines(commands), _lines(events_path), commands)


def _report(m: Mismatch, seed: int, n: int) -> None:
    where = f"seq_no {m.seq}" if m.seq is not None else "final depth (no seq_no)"
    print(f"DIFFTEST MISMATCH: seed {seed}, {n} commands")
    print(f"  first difference at output line {m.output_line}, {where}")
    print(f"  command:  {m.command}")
    print("  preceding reference output:")
    for line in m.context:
        print(f"    {line}")
    print(f"  expected (reference): {m.expected}")
    print(f"  actual   (engine):    {m.actual}")


def difftest(
    seed: int, n: int, driver: Path, workdir: Path, regressions_dir: Path, profile: str = "narrow",
    record: Path | None = None,
) -> Mismatch | None:
    """Generates `n` commands from `seed`, runs both engines and compares their output.
    With `record`, the outcome is written into that JSON file (results/difftest.json:
    the dashboard's Overview reads it), one entry per profile."""
    start = time.monotonic()
    commands = generate(seed, n, profile)
    name = f"seed-{seed}" if profile == "narrow" else f"{profile}-seed-{seed}"
    cmds_path = workdir / f"{name}.cmds"
    cmds_path.write_text("\n".join(commands) + "\n")
    events_path = workdir / f"{name}.engine.events"
    try:
        _run_driver_to(driver, cmds_path, events_path)
    except DriverError as e:
        m = Mismatch(0, None, "<engine output>", f"<driver failed: {e}>", None, [])
        _report(m, seed, n)
        print(f"  saved regression: {save_regression(commands, None, name, regressions_dir)}")
        return m

    m, lines = _compare(run_lines(commands), _lines(events_path), commands)
    if record is not None:
        _record(record, profile, seed, n, lines, m)
    if m is not None:
        _report(m, seed, n)
        print(f"  saved regression: {save_regression(commands, m.seq, name, regressions_dir)}")
        return m
    print(f"difftest ok: {profile} profile, seed {seed}, {n} commands, {lines} output lines identical "
          f"({time.monotonic() - start:.1f}s)")
    return None


def _record(path: Path, profile: str, seed: int, n: int, lines: int, m: Mismatch | None) -> None:
    # Committed with the work it checks: it names the commit that work is based on and
    # whether the tree had changes, not its own commit.
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
    doc = json.loads(path.read_text()) if path.is_file() else {"schema": 1, "runs": {}}
    doc["generated_by"] = "make difftest-long"
    doc["reference"] = "reference/ (the Python reference engine)"
    doc["runs"][profile] = {"seed": seed, "commands": n, "output_lines": lines,
                            "result": "identical" if m is None else f"mismatch at seq_no {m.seq}", "base_commit": commit,
                            "dirty": dirty}
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="C++ engine vs Python reference")
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--n", type=int, default=100_000)
    parser.add_argument("--regressions-dir", type=Path, default=DEFAULT_REGRESSIONS_DIR)
    parser.add_argument("--profile", choices=PROFILES, default="narrow")
    parser.add_argument("--replay", action="store_true", help="replay saved regressions only")
    parser.add_argument("--record", type=Path, help="write the outcome into this JSON file")
    args = parser.parse_args(argv)

    if args.replay:
        failed = 0
        for path in sorted(args.regressions_dir.glob("*.cmds")):
            m = compare_file(path, args.driver)
            print(f"{path.name}: {'MISMATCH at seq_no ' + str(m.seq) if m else 'ok'}")
            failed += m is not None
        return 1 if failed else 0

    with tempfile.TemporaryDirectory() as tmp:
        m = difftest(args.seed, args.n, args.driver, Path(tmp), args.regressions_dir, args.profile, args.record)
    return 1 if m else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
