"""Replays each benchmark workload with the invariant walk on: the workload is journaled
by the bench build (Release, walk off) and replayed by mf-replay from the default build
(walk on after every command). Both digests must equal the pinned one, so the published
numbers come from an engine whose output on that very workload passes every invariant.

Usage: python -m bench.crosscheck [--bench-dir build-bench] [--replay build/engine/tools/mf-replay]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from bench import run

REPO_ROOT = Path(__file__).resolve().parent.parent
N = 200_000


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bench-dir", type=Path, default=REPO_ROOT / "build-bench")
    parser.add_argument("--replay", type=Path, default=REPO_ROOT / "build" / "engine" / "tools" / "mf-replay")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        for profile in run.PROFILES:
            journal = Path(tmp) / f"{profile}.mfj"
            out = subprocess.run(
                [str(args.bench_dir / "bench" / "mf-bench-replay"), "--profile", profile, "--n",
                 str(N), "--mode", "digest", "--journal", str(journal)],
                check=True, capture_output=True, text=True).stdout
            bench_digest = json.loads(out)["digest"]["events"]
            replayed = subprocess.run([str(args.replay), str(journal)], check=True,
                                      capture_output=True, text=True).stdout.strip()
            replay_digest = replayed.removeprefix("sha256=")
            run.check_digest(profile, N, bench_digest)
            run.check_digest(profile, N, replay_digest)
            print(f"crosscheck ok: {profile}, {N:,} commands, invariant walk on: {replay_digest[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
