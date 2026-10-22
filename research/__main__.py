"""MarketForge research CLI (M8).

    python -m research run <config.toml> [--out runs]
    python -m research reproduce <runs/.../record.json> [--work-dir DIR]

Use the release install for real runs (``make backtest CONFIG=...`` runs this from
``.venv-release``); the dev install checks the book's invariants after every command.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import tomllib
from pathlib import Path

import mfengine

from research import runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research", description="Run and reproduce backtests.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run a config and store its record")
    p_run.add_argument("config", type=Path)
    p_run.add_argument("--out", type=Path, default=Path("runs"))
    p_run.add_argument("--name", help="run directory name (default: <kind>-<run id>)")
    p_rep = sub.add_parser("reproduce", help="re-run a stored record and compare its results")
    p_rep.add_argument("record", type=Path)
    p_rep.add_argument("--work-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    if mfengine.INVARIANT_CHECKS:
        print("warning: mfengine invariant checks are on (the dev install): correct but slow; "
              "use `make venv-release` and `make backtest` for real runs", file=sys.stderr)
    if args.command == "run":
        try:
            record = runs.run(tomllib.loads(args.config.read_text()), out_dir=args.out, name=args.name)
        except runs.ConfigError as err:
            print(f"{args.config}: {err}", file=sys.stderr)
            return 2
        print(record.path)
        return 0
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="mf-reproduce-"))
    rep = runs.reproduce(args.record, work_dir=work)
    for w in rep.warnings:
        print(f"note: {w}", file=sys.stderr)
    if rep.identical:
        print(f"identical: {rep.results_sha256}")
        return 0
    print(f"DIFFERENT: stored {rep.stored_sha256}, now {rep.results_sha256} ({rep.results_path})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
