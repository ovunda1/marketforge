"""Required fields of the results files in bench/results/. A missing field is an error
that names every missing path, so a results file can never silently lose its machine
spec, flags or spread."""

from __future__ import annotations

import copy
from typing import Any

PROFILES = ("narrow", "wide")
SUMMARY = ("n", "median", "min", "max", "cv")


class SchemaError(ValueError):
    pass


MACHINE = [
    "machine.cpu", "machine.model", "machine.performance_cores", "machine.efficiency_cores",
    "machine.logical_cpus", "machine.memory_bytes", "machine.os", "machine.power.source",
    "machine.power.low_power_mode", "machine.power.forced", "machine.thermal",
]
BUILD = ["build.compiler", "build.build_type", "build.cxx_flags", "build.lto",
         "build.invariant_checks", "build.system"]
GIT = ["git.commit", "git.dirty"]


def _summary(prefix: str) -> list[str]:
    return [f"{prefix}.{k}" for k in SUMMARY]


def _replay_profile(p: str) -> list[str]:
    base = f"profiles.{p}"
    paths = [f"{base}.digest.commands", f"{base}.digest.events",
             f"{base}.allocations_per_command", f"{base}.workload.max_resting",
             f"{base}.throughput.reps", f"{base}.latency.reps", f"{base}.latency.merged.p99",
             f"{base}.cross_check.relative_difference", f"{base}.scheduling"]
    paths += _summary(f"{base}.throughput.commands_per_sec")
    paths += _summary(f"{base}.throughput.events_per_sec")
    for q in ("p50", "p90", "p99", "p999", "max", "mean"):
        paths += _summary(f"{base}.latency.{q}")
    return paths


REPLAY = [*MACHINE, *BUILD, *GIT, "timer.clock", "timer.p50_ns", "timer.p99_ns", "timer.max_ns",
          "timer.min_step_ns", "timer.zero_fraction", "timer.cost_ns", "settings.seed",
          "settings.n", "settings.reps", "settings.warmup", "settings.generator_version"]
for _p in PROFILES:
    REPLAY += _replay_profile(_p)

MICRO = [*MACHINE, *BUILD, *GIT, "settings.reps", "settings.min_time", "settings.batch",
         "benchmarks"]

AB = [*MACHINE, *BUILD, "id", "title", "before.ref", "before.commit", "after.ref", "after.commit",
      "settings.rounds", "settings.n", "micro"]
for _p in PROFILES:
    base = f"profiles.{_p}"
    AB += [f"{base}.digest_match", f"{base}.allocations_per_command.before",
           f"{base}.allocations_per_command.after", f"{base}.throughput.before",
           f"{base}.throughput.after", f"{base}.throughput.verdict",
           *[f"{base}.throughput.ratio.{k}" for k in ("n", "median", "min", "max")]]
    for _q in ("p50", "p99", "p999"):
        AB += [f"{base}.latency.{_q}.before", f"{base}.latency.{_q}.after",
               f"{base}.latency.{_q}.verdict",
               *[f"{base}.latency.{_q}.ratio.{k}" for k in ("n", "median", "min", "max")]]


def _missing(doc: Any, paths: list[str]) -> list[str]:
    missing = []
    for path in paths:
        node = doc
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                missing.append(path)
                break
            node = node[key]
    return missing


def _validate(doc: dict, paths: list[str], what: str) -> None:
    missing = _missing(doc, paths)
    if missing:
        # Report a missing profile once rather than every path under it.
        shown: list[str] = []
        for m in missing:
            parts = m.split(".")
            if parts[0] == "profiles" and len(parts) > 1 and parts[1] not in doc.get("profiles", {}):
                m = f"profiles.{parts[1]}"
            if m not in shown:
                shown.append(m)
        raise SchemaError(f"{what}: missing " + ", ".join(shown))


def validate_replay(doc: dict) -> None:
    _validate(doc, REPLAY, "replay results")


def validate_micro(doc: dict) -> None:
    _validate(doc, MICRO, "micro results")
    paths = []
    for name in doc["benchmarks"]:
        paths += _summary(f"benchmarks.{name}.ns_per_op")
    if not doc["benchmarks"]:
        raise SchemaError("micro results: no benchmarks")
    _validate_nested(doc, paths, "micro results")


def _validate_nested(doc: dict, paths: list[str], what: str) -> None:
    # Benchmark names contain '/', never '.', so dotted paths still split correctly.
    missing = _missing(doc, paths)
    if missing:
        raise SchemaError(f"{what}: missing " + ", ".join(missing))


def validate_ab(doc: dict) -> None:
    _validate(doc, AB, "A/B results")


VALIDATORS = {"replay": validate_replay, "micro": validate_micro, "ab": validate_ab}


def _example_from(paths: list[str]) -> dict:
    doc: dict = {}
    for path in paths:
        node = doc
        keys = path.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node.setdefault(keys[-1], 1)
    return doc


def _examples() -> dict[str, dict]:
    micro = _example_from(MICRO)
    micro["benchmarks"] = {"AddExistingLevel": {"ns_per_op": {k: 1 for k in SUMMARY}}}
    return {"replay": _example_from(REPLAY), "micro": micro, "ab": _example_from(AB)}


EXAMPLES = _examples()


def example(name: str) -> dict:
    return copy.deepcopy(EXAMPLES[name])
