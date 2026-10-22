"""Runs every benchmark and writes bench/results/{replay,micro}.json, then re-renders the
docs. `make bench` calls this; `make bench-smoke` runs it with --smoke (tiny sizes, a
temporary output directory, schema checks only, no speed assertions).

Results are refused (exit 2) when the machine is on battery or in Low Power Mode, unless
--force, which is recorded in the results. Each profile's event digest must match
bench/expected_digests.json: a benchmark of a wrong engine is not published.

Usage: python -m bench.run [--build-dir build-bench] [--smoke] [--force]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from bench import machine, render, schema, stats

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "bench" / "results"
PROFILES = ("narrow", "wide")


def expected_digests() -> dict:
    return json.loads((REPO_ROOT / "bench" / "expected_digests.json").read_text())


def check_digest(profile: str, n: int, digest: str) -> None:
    expected = expected_digests().get(profile, {}).get(str(n))
    if expected is None:
        raise SystemExit(f"no pinned digest for {profile} n={n} in bench/expected_digests.json")
    if digest != expected:
        raise SystemExit(f"{profile} n={n}: event digest {digest} != pinned {expected}: "
                         "the engine's output changed; results not written")


def run_replay(build_dir: Path, profile: str, n: int, reps: int, cooldown_ms: int,
               extra: list[str] | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "replay.json"
        cmd = [str(build_dir / "bench" / "mf-bench-replay"), "--profile", profile, "--n", str(n),
               "--reps", str(reps), "--cooldown-ms", str(cooldown_ms), "--out", str(out),
               *(extra or [])]
        subprocess.run(cmd, check=True)
        return json.loads(out.read_text())


def run_micro(build_dir: Path, reps: int, min_time: str, filter_: str | None = None) -> dict:
    """Google Benchmark output, summarised per benchmark as time per operation."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "micro.json"
        cmd = [str(build_dir / "bench" / "mf-bench-micro"), f"--benchmark_repetitions={reps}",
               f"--benchmark_min_time={min_time}", "--benchmark_enable_random_interleaving=true",
               f"--benchmark_out={out}", "--benchmark_out_format=json"]
        if filter_:
            cmd.append(f"--benchmark_filter={filter_}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        raw = json.loads(out.read_text())
    per_op: dict[str, list[float]] = {}
    batch = None
    for b in raw["benchmarks"]:
        if b.get("run_type") != "iteration":
            continue
        name = b["run_name"].removesuffix("/manual_time")
        assert b["time_unit"] == "ns", b["time_unit"]
        batch = int(b["batch"])
        per_op.setdefault(name, []).append(b["real_time"] / batch)
    return {"context": raw["context"], "batch": batch,
            "benchmarks": {name: {"ns_per_op": stats.summary(v), "runs": v}
                           for name, v in per_op.items()}}


def build_of(micro_context: dict) -> dict:
    return json.loads(micro_context["mf_build"])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build-dir", type=Path, default=REPO_ROOT / "build-bench")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--force", action="store_true", help="measure even on battery")
    parser.add_argument("--n", type=int, default=5_000_000)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--micro-reps", type=int, default=10)
    parser.add_argument("--micro-min-time", default="0.2s")
    args = parser.parse_args(argv)

    if args.smoke:
        args.n, args.reps, args.micro_reps, args.micro_min_time = 20_000, 1, 1, "0.001s"
        cooldown = 0
    else:
        cooldown = 500
        problem = machine.power_problem(machine.power())
        if problem and not args.force:
            print(f"bench: refusing to measure: {problem} (or pass --force / FORCE=1)")
            return 2

    mach = machine.machine(forced=args.force)
    git = machine.git_info()

    profiles = {}
    first = None
    for profile in PROFILES:
        print(f"bench: replay {profile}, {args.n:,} commands x {args.reps}", flush=True)
        r = run_replay(args.build_dir, profile, args.n, args.reps, cooldown)
        check_digest(profile, args.n, r["digest"]["events"])
        first = first or r
        profiles[profile] = {k: r[k] for k in ("digest", "allocations_per_command", "workload",
                                               "throughput", "latency", "cross_check",
                                               "calibration", "scheduling")}
    replay = {
        "schema": 1, "generated_by": "make bench", "machine": mach, "git": git,
        "build": first["build"], "timer": first["timer"],
        "settings": {k: first["settings"][k] for k in ("seed", "n", "reps", "warmup",
                                                       "generator_version", "cooldown_ms",
                                                       "tolerance", "max_attempts")},
        "profiles": profiles,
    }
    schema.validate_replay(replay)

    print(f"bench: microbenchmarks x {args.micro_reps}", flush=True)
    m = run_micro(args.build_dir, args.micro_reps, args.micro_min_time)
    micro = {
        "schema": 1, "generated_by": "make bench", "machine": mach, "git": git,
        "build": build_of(m["context"]),
        "settings": {"reps": args.micro_reps, "min_time": args.micro_min_time, "batch": m["batch"],
                     "random_interleaving": True},
        "library": {k: m["context"].get(k) for k in ("library_version", "library_build_type")},
        "benchmarks": {name: {"ns_per_op": b["ns_per_op"]} for name, b in m["benchmarks"].items()},
    }
    schema.validate_micro(micro)

    out_dir = Path(tempfile.mkdtemp(prefix="mf-bench-smoke-")) if args.smoke else RESULTS
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "replay.json").write_text(json.dumps(replay, indent=2) + "\n")
    (out_dir / "micro.json").write_text(json.dumps(micro, indent=2) + "\n")
    print(f"bench: wrote {out_dir / 'replay.json'} and {out_dir / 'micro.json'}")
    if not args.smoke:
        for path in render.render_all():
            print(f"bench: rendered {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
