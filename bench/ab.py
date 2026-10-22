"""A/B comparison of two tagged builds for bench/OPTIMIZATIONS.md.

Each side of an optimization (its before and after tags, bench/optimizations.json) is
checked out into a git worktree under .bench-ab/ and built like `make bench`, with the
current bench/ copied over the tag's: both sides run the same measurement code, and only
the engine (and its build flags) differs. The two
builds then run in interleaved rounds (A B, B A, A B, ...) so that drift in temperature
or clock speed hits both equally. Every round runs both workloads end to end (one
repetition each: throughput and latency) and the microbenchmarks. Ratios are paired by
round; a verdict needs every round to agree (bench/stats.py).

Before and after must produce the same event digest: an optimization that changes the
engine's output is a bug, not a speedup.

Usage: python -m bench.ab <opt-id>|all [--rounds 7] [--n 2000000] [--force]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from bench import machine, run, schema, stats

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKTREES = REPO_ROOT / ".bench-ab"
MAIN_DEPS = REPO_ROOT / "build-bench" / "_deps"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def build(ref: str) -> tuple[Path, str]:
    """Checks out `ref` into a worktree and builds its benchmarks; returns the build dir."""
    commit = _git("rev-parse", f"{ref}^{{commit}}")
    tree = WORKTREES / ref
    if not tree.exists():
        WORKTREES.mkdir(exist_ok=True)
        _git("worktree", "add", "--detach", str(tree), commit)
    else:
        subprocess.run(["git", "-C", str(tree), "checkout", "--quiet", "--force", "--detach", commit],
                       check=True)
    # The same harness on both sides: today's bench/ over the tag's.
    shutil.copytree(REPO_ROOT / "bench", tree / "bench", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("results", "__pycache__", "*.md", "*.md.in"))
    build_dir = tree / "build-bench"
    cmd = ["cmake", "-S", str(tree), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release",
           "-DMF_CHECK_INVARIANTS=OFF", "-DMF_BUILD_BENCH=ON", "-DMF_BUILD_TESTS=OFF"]
    # Reuse the main bench build's downloaded sources (pinned by hash there too).
    for dep, var in (("googlebenchmark", "GOOGLEBENCHMARK"), ("hdrhistogram", "HDRHISTOGRAM"),
                     ("yaml-cpp", "YAML-CPP")):
        src = MAIN_DEPS / f"{dep}-src"
        if src.exists():
            cmd.append(f"-DFETCHCONTENT_SOURCE_DIR_{var}={src}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["cmake", "--build", str(build_dir), "-j", "--target", "mf-bench-replay",
                    "mf-bench-micro"], check=True, stdout=subprocess.DEVNULL)
    return build_dir, commit


def compare(opt: dict, rounds: int, n: int, forced: bool) -> dict:
    sides = {}
    for side in ("before", "after"):
        build_dir, commit = build(opt[side])
        sides[side] = {"dir": build_dir, "commit": commit}
        print(f"ab: built {side} = {opt[side]} ({commit[:12]})", flush=True)

    per = {side: {"replay": {p: [] for p in run.PROFILES}, "micro": []} for side in sides}
    for r in range(rounds):
        order = ("before", "after") if r % 2 == 0 else ("after", "before")
        for side in order:
            for p in run.PROFILES:
                per[side]["replay"][p].append(
                    run.run_replay(sides[side]["dir"], p, n, reps=1, cooldown_ms=300))
            per[side]["micro"].append(
                run.run_micro(sides[side]["dir"], reps=1, min_time="0.1s", filter_=opt.get("micro_filter")))
        print(f"ab: round {r + 1}/{rounds} done", flush=True)

    profiles = {}
    for p in run.PROFILES:
        b, a = per["before"]["replay"][p], per["after"]["replay"][p]
        digests = {x["digest"]["events"] for x in b + a}
        run.check_digest(p, n, next(iter(digests)))
        tp_b = [x["throughput"]["commands_per_sec"]["median"] for x in b]
        tp_a = [x["throughput"]["commands_per_sec"]["median"] for x in a]
        tp_ratio = stats.ratio_summary(tp_b, tp_a)
        latency = {}
        for q in ("p50", "p99", "p999"):
            lb = [x["latency"][q]["median"] for x in b]
            la = [x["latency"][q]["median"] for x in a]
            ratio = stats.ratio_summary(lb, la)
            latency[q] = {"before": lb, "after": la, "ratio": ratio,
                          "verdict": stats.verdict(ratio, higher_is_better=False)}
        profiles[p] = {
            "digest_match": len(digests) == 1,
            "throughput": {"before": tp_b, "after": tp_a, "ratio": tp_ratio,
                           "verdict": stats.verdict(tp_ratio, higher_is_better=True)},
            "latency": latency,
            "allocations_per_command": {"before": b[0]["allocations_per_command"],
                                        "after": a[0]["allocations_per_command"]},
            "suspect_reps": sum(rep["suspect"] for x in b + a
                                for kind in ("throughput", "latency") for rep in x[kind]["reps"]),
        }

    micro = {}
    names = set(per["before"]["micro"][0]["benchmarks"]) & set(per["after"]["micro"][0]["benchmarks"])
    for name in sorted(names):
        mb = [m["benchmarks"][name]["ns_per_op"]["median"] for m in per["before"]["micro"]]
        ma = [m["benchmarks"][name]["ns_per_op"]["median"] for m in per["after"]["micro"]]
        ratio = stats.ratio_summary(mb, ma)
        micro[name] = {"before": mb, "after": ma, "ratio": ratio,
                       "verdict": stats.verdict(ratio, higher_is_better=False)}

    after_first = per["after"]["replay"][run.PROFILES[0]][0]
    return {
        "schema": 1, "id": opt["id"], "title": opt["title"],
        "before": {"ref": opt["before"], "commit": sides["before"]["commit"],
                   "build": per["before"]["replay"][run.PROFILES[0]][0]["build"]},
        "after": {"ref": opt["after"], "commit": sides["after"]["commit"]},
        "harness": machine.git_info(),
        "machine": machine.machine(forced=forced), "build": after_first["build"],
        "timer": after_first["timer"],
        "settings": {"rounds": rounds, "n": n, "reps_per_round": 1, "micro_min_time": "0.1s",
                     "seed": after_first["settings"]["seed"]},
        "profiles": profiles, "micro": micro,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("opt", help="optimization id from bench/optimizations.json, or 'all'")
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--n", type=int, default=2_000_000)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    problem = machine.power_problem(machine.power())
    if problem and not args.force:
        print(f"ab: refusing to measure: {problem} (or pass --force)")
        return 2
    manifest = json.loads((REPO_ROOT / "bench" / "optimizations.json").read_text())
    opts = [o for o in manifest["optimizations"] if args.opt in ("all", o["id"])]
    if not opts:
        print(f"ab: no optimization {args.opt!r} in bench/optimizations.json")
        return 1
    for opt in opts:
        doc = compare(opt, args.rounds, args.n, args.force)
        schema.validate_ab(doc)
        out = REPO_ROOT / "bench" / "results" / "ab" / f"{opt['id']}.json"
        out.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"ab: wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
