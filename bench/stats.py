"""A/B statistics: per-round ratios, a verdict that needs the whole range on one side."""

from __future__ import annotations

import math
import statistics


def ratio_summary(before: list[float], after: list[float]) -> dict:
    """Ratios after / before of paired rounds (round i of each ran back to back)."""
    if len(before) != len(after) or not before:
        raise ValueError("ratio_summary needs two non-empty lists of the same length")
    ratios = [a / b for a, b in zip(after, before, strict=True)]
    return {"n": len(ratios), "median": statistics.median(ratios), "min": min(ratios),
            "max": max(ratios)}


def verdict(summary: dict, *, higher_is_better: bool) -> str:
    """'faster' or 'slower' only if every round agrees; otherwise no measurable effect."""
    lo, hi = summary["min"], summary["max"]
    if higher_is_better:
        better, worse = lo > 1, hi < 1
    else:
        better, worse = hi < 1, lo > 1
    return "faster" if better else "slower" if worse else "no measurable effect"


def geomean(values: list[float]) -> float:
    return math.exp(sum(math.log(v) for v in values) / len(values))


def summary(values: list[float]) -> dict:
    """Same fields as the C++ Summary (bench/lib/stats.hpp)."""
    mean = statistics.fmean(values)
    stddev = statistics.pstdev(values)
    return {"n": len(values), "median": statistics.median(values), "min": min(values),
            "max": max(values), "mean": mean, "stddev": stddev,
            "cv": stddev / mean if mean else 0.0}
