"""Renders the benchmark docs from bench/results/*.json, so no number is ever typed.

A template (X.md.in) holds prose and placeholders; X.md is generated next to it.

  {{ replay:profiles.narrow.latency.p99.median|ns }}   one value, formatted
  {{ ...|ns|bare }}                                     no workload label (table rows only)
  {{ block:latency_table }}                             a generated table

A value under a workload profile is labelled with it ("412 ns (narrow workload)"), so a
number can never lose the workload it was measured on. lint() rejects typed numbers in a
template: digits outside placeholders and code spans.

Usage: python -m bench.render   (re-renders every template)
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bench import stats

PROFILES = ("narrow", "wide")
PLACEHOLDER = re.compile(r"\{\{\s*(.*?)\s*\}\}")
NUMBER = re.compile(r"(?<![\w\-./+#])\d[\d,]*(?:\.\d+)?")
REPO_ROOT = Path(__file__).resolve().parent.parent


class RenderError(Exception):
    pass


def _ns(v: float) -> str:
    v = float(v)
    if v < 1_000:
        return f"{v:.0f} ns"
    if v < 1_000_000:
        return f"{v / 1e3:.2f} µs"
    return f"{v / 1e6:.2f} ms"


FORMATS: dict[str, Callable[[Any], str]] = {
    "ns": _ns,
    "mps": lambda v: f"{v / 1e6:.2f} M/s",
    "int": lambda v: f"{round(v):,}",
    "pct": lambda v: f"{(v - 1) * 100:+.1f}%",  # a ratio as a change: 1.23 -> +23.0%
    "pct_signed": lambda v: f"{v * 100:+.1f}%",  # a relative difference: -0.2 -> -20.0%
    "ratio": lambda v: f"{v:.2f}×",
    "fraction": lambda v: f"{v * 100:.0f}%",
    "f1": lambda v: f"{v:.1f}",
    "f2": lambda v: f"{v:.2f}",
    "gib": lambda v: f"{v / 2**30:.0f} GiB",
    "text": str,
}


def _lookup(sources: dict, src: str, path: str) -> Any:
    if src not in sources:
        raise RenderError(f"unknown source {src!r}")
    node = sources[src]
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise RenderError(f"{src}:{path} not found")
        node = node[key]
    return node


def _value(spec: str, sources: dict, line: str) -> str:
    ref, *mods = [part.strip() for part in spec.split("|")]
    if ":" not in ref:
        raise RenderError(f"placeholder {spec!r} has no source")
    src, path = ref.split(":", 1)
    value = _lookup(sources, src, path)
    fmt = mods[0] if mods else "text"
    flags = set(mods[1:])
    if fmt not in FORMATS:
        raise RenderError(f"unknown format {fmt!r} in {spec!r}")
    text = FORMATS[fmt](value)
    profile = next((seg for seg in path.split(".") if seg in PROFILES), None)
    if "bare" in flags:
        if not line.lstrip().startswith("|"):
            raise RenderError(f"'bare' outside a table row: {spec!r}")
    elif profile:
        text += f" ({profile} workload)"
    return text


def render(template: str, sources: dict) -> str:
    out = []
    for line in template.splitlines(keepends=True):
        def sub(m: re.Match[str], line: str = line) -> str:
            spec = m.group(1)
            if spec.startswith("block:"):
                _, name, *arg = spec.split(":", 2)
                if name not in BLOCKS:
                    raise RenderError(f"unknown block {name!r}")
                return BLOCKS[name](sources, arg[0] if arg else "")
            return _value(spec, sources, line)

        out.append(PLACEHOLDER.sub(sub, line))
    return "".join(out)


def lint(template: str) -> list[str]:
    """Typed numbers in a template: digits outside placeholders, code spans and list
    markers, not part of an identifier (p99, M6, SHA-256, x86_64)."""
    found = []
    for line in template.splitlines():
        s = PLACEHOLDER.sub(" ", line)
        s = re.sub(r"`[^`]*`", " ", s)
        s = re.sub(r"^\s*\d+\.\s", " ", s)
        found += [f"{line.strip()!r}: {m.group(0)}" for m in NUMBER.finditer(s)]
    return found


# --- Blocks -------------------------------------------------------------------------------


def _range(s: dict, fmt: str) -> str:
    f = FORMATS[fmt]
    return f"{f(s['median'])} ({f(s['min'])} – {f(s['max'])})"


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def machine_table(sources: dict, src: str) -> str:
    d = sources[src or "replay"]
    m, b = d["machine"], d["build"]
    power = m["power"]
    rows = [
        ["CPU", f"{m['cpu']} ({m['model']})"],
        ["Cores", f"{m['performance_cores']} performance + {m['efficiency_cores']} efficiency"
                  if m["performance_cores"] is not None else f"{m['logical_cpus']} logical"],
        ["Memory", FORMATS["gib"](m["memory_bytes"])],
        ["OS", m["os"]],
        ["Compiler", b["compiler"]],
        ["Build", f"{b['build_type']}, flags `{b['cxx_flags']}`, LTO {'on' if b['lto'] else 'off'}, "
                  f"invariant walk {'on' if b['invariant_checks'] else 'off'}"],
        ["Power", f"{power['source']}, Low Power Mode "
                  f"{ {True: 'on', False: 'off', None: 'n/a'}[power['low_power_mode']] }"],
        ["Commit", f"`{d['git']['commit'][:12]}`{' (uncommitted changes)' if d['git']['dirty'] else ''}"],
    ]
    if "timer" in d:
        t = d["timer"]
        rows.append(["Clock", f"`{t['clock']}`: {FORMATS['f1'](t['cost_ns'])} ns per read, "
                              f"step {t['min_step_ns']} ns"])
    return _table(["", ""], rows)


def throughput_table(sources: dict, _: str) -> str:
    d = sources["replay"]
    rows = []
    for p in PROFILES:
        t = d["profiles"][p]["throughput"]
        rows.append([p, _range(t["commands_per_sec"], "mps"), _range(t["events_per_sec"], "mps"),
                     FORMATS["fraction"](t["commands_per_sec"]["cv"]) if t["commands_per_sec"]["cv"] >= 0.005
                     else "<1%", str(t["commands_per_sec"]["n"])])
    return _table(["Workload", "Commands/s, median (min – max)", "Events/s, median (min – max)",
                   "CV", "Reps"], rows)


def latency_table(sources: dict, _: str) -> str:
    d = sources["replay"]
    rows = []
    for p in PROFILES:
        lat = d["profiles"][p]["latency"]
        rows.append([p, *[_range(lat[q], "ns") for q in ("p50", "p90", "p99", "p999")],
                     FORMATS["ns"](lat["max"]["max"])])
    return _table(["Workload", "p50", "p90", "p99", "p99.9", "max (worst rep)"], rows)


# Registration order of bench/micro.cpp; the JSON follows run order, which is shuffled.
MICRO_ORDER = ["AddExistingLevel", "AddNewLevel", "CancelKeepsLevel", "CancelRemovesLevel",
               "ReplaceQtyDown", "ReplaceReprice", "MatchOneLevel/1", "MatchOneLevel/8",
               "MatchMultiLevel/2", "MatchMultiLevel/8", "MatchMultiLevel/32", "RiskCheck",
               "RiskCheckNoLimits", "EngineAdd"]


def _micro_sorted(names) -> list[str]:
    return sorted(names, key=lambda n: (MICRO_ORDER.index(n) if n in MICRO_ORDER else len(MICRO_ORDER), n))


def micro_table(sources: dict, _: str) -> str:
    d = sources["micro"]
    rows = [[f"`{name}`", _range(d["benchmarks"][name]["ns_per_op"], "ns"),
             FORMATS["fraction"](d["benchmarks"][name]["ns_per_op"]["cv"])]
            for name in _micro_sorted(d["benchmarks"])]
    return _table(["Benchmark", "Time per operation, median (min – max)", "CV"], rows)


def _ratio(r: dict) -> str:
    return f"{FORMATS['ratio'](r['median'])} ({FORMATS['ratio'](r['min'])} – {FORMATS['ratio'](r['max'])})"


def ab_table(sources: dict, opt: str) -> str:
    d = sources["ab"][opt]
    rows = []
    for p in PROFILES:
        pr = d["profiles"][p]
        t = pr["throughput"]
        rows.append([p, "commands/s", FORMATS["mps"](stats.summary(t["before"])["median"]),
                     FORMATS["mps"](stats.summary(t["after"])["median"]), _ratio(t["ratio"]),
                     t["verdict"]])
        for q, label in (("p50", "p50"), ("p99", "p99"), ("p999", "p99.9")):
            lat = pr["latency"][q]
            rows.append([p, f"latency {label}", FORMATS["ns"](stats.summary(lat["before"])["median"]),
                         FORMATS["ns"](stats.summary(lat["after"])["median"]), _ratio(lat["ratio"]),
                         lat["verdict"]])
        a = pr["allocations_per_command"]
        rows.append([p, "heap allocations per command", FORMATS["f2"](a["before"]),
                     FORMATS["f2"](a["after"]), "", ""])
    return _table(["Workload", "Metric", "Before", "After", "After / before, median (min – max)",
                   "Verdict"], rows)


def ab_micro_table(sources: dict, opt: str) -> str:
    d = sources["ab"][opt]
    rows = []
    for name in _micro_sorted(d["micro"]):
        m = d["micro"][name]
        rows.append([f"`{name}`", FORMATS["ns"](stats.summary(m["before"])["median"]),
                     FORMATS["ns"](stats.summary(m["after"])["median"]), _ratio(m["ratio"]),
                     m["verdict"]])
    return _table(["Benchmark (time per op)", "Before", "After", "After / before", "Verdict"], rows)


def ab_settings(sources: dict, opt: str) -> str:
    d = sources["ab"][opt]
    s = d["settings"]
    return (f"{s['rounds']} interleaved rounds per build, {FORMATS['int'](s['n'])} commands per "
            f"workload per round; before `{d['before']['ref']}` (`{d['before']['commit'][:12]}`), "
            f"after `{d['after']['ref']}` (`{d['after']['commit'][:12]}`)")


def ab_geomean(sources: dict, opt: str) -> str:
    d = sources["ab"][opt]
    g = stats.geomean([d["profiles"][p]["throughput"]["ratio"]["median"] for p in PROFILES])
    return FORMATS["ratio"](g)


def headline(sources: dict, _: str) -> str:
    d = sources["replay"]
    lines = []
    for p in PROFILES:
        pr = d["profiles"][p]
        lines.append(
            f"- **{p} workload**: {FORMATS['mps'](pr['throughput']['commands_per_sec']['median'])} "
            f"commands ({FORMATS['mps'](pr['throughput']['events_per_sec']['median'])} events) "
            f"end to end through the risk gate and book; per-command latency p50 "
            f"{FORMATS['ns'](pr['latency']['p50']['median'])}, p99 "
            f"{FORMATS['ns'](pr['latency']['p99']['median'])}, p99.9 "
            f"{FORMATS['ns'](pr['latency']['p999']['median'])}"
        )
    lines.append(f"- Latency percentiles are quantised to the clock's step of "
                 f"{FORMATS['ns'](d['timer']['min_step_ns'])}: a p50 of one step means most commands "
                 f"finish within one tick (see Reading the low percentiles in `bench/RESULTS.md`)")
    m = d["machine"]
    lines.append(f"- Measured on {m['cpu']}, {d['build']['compiler']}, "
                 f"`{d['build']['cxx_flags']}`; median of {d['settings']['reps']} repetitions "
                 f"of {FORMATS['int'](d['settings']['n'])} commands each")
    return "\n".join(lines)


BLOCKS: dict[str, Callable[[dict, str], str]] = {
    "machine_table": machine_table, "throughput_table": throughput_table,
    "latency_table": latency_table, "micro_table": micro_table, "ab_table": ab_table,
    "ab_micro_table": ab_micro_table, "ab_settings": ab_settings, "ab_geomean": ab_geomean,
    "headline": headline,
}


# --- Files --------------------------------------------------------------------------------


def load_sources(root: Path = REPO_ROOT) -> dict:
    results = root / "bench" / "results"
    sources: dict[str, Any] = {"ab": {}}
    for name in ("replay", "micro"):
        path = results / f"{name}.json"
        if path.exists():
            sources[name] = json.loads(path.read_text())
    for path in sorted((results / "ab").glob("*.json")):
        sources["ab"][path.stem] = json.loads(path.read_text())
    sources["manifest"] = json.loads((root / "bench" / "optimizations.json").read_text())
    return sources


def templates(root: Path = REPO_ROOT) -> list[Path]:
    return sorted([*(root / "bench").glob("*.md.in"), *(root / "docs" / "learning").glob("*.md.in")])


def render_all(root: Path = REPO_ROOT) -> list[Path]:
    sources = load_sources(root)
    written = []
    for template in templates(root):
        problems = lint(template.read_text())
        if problems:
            raise RenderError(f"{template.name}: typed numbers:\n  " + "\n  ".join(problems))
        target = template.with_suffix("")
        target.write_text(render(template.read_text(), sources))
        written.append(target)
    return written


def main() -> int:
    for path in render_all():
        print(f"rendered {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
