"""Per-command cost of the Python bindings (M7): one Engine.process() call per command
vs one Engine.process_batch() for all of them, on whichever mfengine is installed.

The workload is the differential-test generator's stream (reference/generator.py), with
the commands built as Python objects before timing starts. Each repetition runs on a
fresh engine; repetitions alternate single and batch so drift hits both. Reported per
command: single calls; the batch alone; the batch plus reading every event back as a
Python object (what a backtest that looks at every event pays); and building the
CommandBatch from Python objects.

Usage: python -m bench.bindings --out bench/results/bindings-dev.json [--force]
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import mfengine as mf
from bench import machine, stats
from reference import commands as ref
from reference.generator import generate

SEED = 20261020
WORKLOAD = "difftest"  # reference/generator.py, narrow profile


def build_commands(n: int) -> list[mf.Command]:
    sides = {"B": mf.Side.BUY, "S": mf.Side.SELL}
    types = {"LMT": mf.OrderType.LIMIT, "MKT": mf.OrderType.MARKET}
    tifs = {"GTC": mf.TimeInForce.GTC, "IOC": mf.TimeInForce.IOC, "FOK": mf.TimeInForce.FOK}
    stps = {"CN": mf.StpMode.CANCEL_NEWEST, "CO": mf.StpMode.CANCEL_OLDEST, "CB": mf.StpMode.CANCEL_BOTH}
    out: list[mf.Command] = []
    for line in generate(SEED, n):
        c = ref.parse_command(line)
        if c is None:
            continue
        if isinstance(c, ref.NewOrder):
            out.append(mf.NewOrder(seq_no=c.seq, order_id=c.order_id, account_id=c.account,
                                   side=sides[c.side], type=types[c.type], tif=tifs[c.tif],
                                   post_only=c.post_only, stp=stps[c.stp], price=c.price, qty=c.qty))
        elif isinstance(c, ref.Cancel):
            out.append(mf.Cancel(seq_no=c.seq, order_id=c.order_id))
        else:
            out.append(mf.Replace(seq_no=c.seq, order_id=c.order_id, price=c.price, qty=c.qty))
    return out


def time_single(commands: list[mf.Command]) -> tuple[int, int]:
    engine = mf.Engine()
    process = engine.process
    events = 0
    start = time.perf_counter_ns()
    for c in commands:
        events += len(process(c))
    return time.perf_counter_ns() - start, events


def time_batch(commands: list[mf.Command]) -> tuple[int, int, int, int]:
    t0 = time.perf_counter_ns()
    batch = mf.CommandBatch()
    batch.extend((c, 0) for c in commands)
    t1 = time.perf_counter_ns()
    result = mf.Engine().process_batch(batch)
    t2 = time.perf_counter_ns()
    events = list(result)
    t3 = time.perf_counter_ns()
    return t1 - t0, t2 - t1, t3 - t2, len(events)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=200_000)
    parser.add_argument("--reps", type=int, default=7)
    parser.add_argument("--force", action="store_true", help="measure even on battery")
    args = parser.parse_args(argv)

    problem = machine.power_problem(machine.power())
    if problem and not args.force:
        print(f"bench: refusing to measure: {problem} (or pass --force / FORCE=1)")
        return 2

    commands = build_commands(args.n)
    n = len(commands)
    time_single(commands[:10_000])  # warm-up: caches, branch predictors, allocator
    time_batch(commands[:10_000])
    single, batch_only, batch_read, build = [], [], [], []
    events_single = events_batch = 0
    for _ in range(args.reps):
        ns, events_single = time_single(commands)
        single.append(ns / n)
        b, p, r, events_batch = time_batch(commands)
        build.append(b / n)
        batch_only.append(p / n)
        batch_read.append((p + r) / n)
    assert events_single == events_batch, (events_single, events_batch)

    single_s, batch_s = stats.summary(single), stats.summary(batch_only)
    doc = {
        "schema": 1,
        "generated_by": "make bench-bindings",
        "machine": machine.machine(forced=args.force),
        "git": machine.git_info(),
        "build": {"build_type": mf.BUILD_TYPE, "invariant_checks": mf.INVARIANT_CHECKS,
                  "python": platform.python_version(), "mfengine": mf.version()},
        "settings": {"workload": WORKLOAD, "seed": SEED, "n": n, "reps": args.reps,
                     "events": events_batch},
        "ns_per_command": {
            "single": single_s,
            "batch": batch_s,
            "batch_and_read_events": stats.summary(batch_read),
            "build_batch": stats.summary(build),
        },
        "single_over_batch": single_s["median"] / batch_s["median"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"bench: {args.out}: single {single_s['median']:.0f} ns/cmd, batch "
          f"{batch_s['median']:.0f} ns/cmd ({doc['single_over_batch']:.1f}x)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
