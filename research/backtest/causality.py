"""A mechanical look-ahead check for bar strategies (M8).

A vectorized strategy computes all its weights from the whole panel at once, so nothing
in the call stops it from reading bar ``t + 1`` while deciding bar ``t``. This makes the
property testable instead: a causal strategy's weights up to bar ``t`` must not change
when

1. everything after ``t`` is removed (``panel.upto(t)``), and
2. everything after ``t`` is replaced with garbage bars.

Both must hold **exactly** (bit for bit). The second catches strategies that use the
future only through the panel's shape (full-sample statistics, normalization). The
strategy tests run it, and every sweep runs it at sampled cut points before using a
strategy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
from typing import Protocol

import numpy as np

from research.backtest.bars import ARRAYS, BarPanel
from research.backtest.strategy import LookAheadError


class BarStrategy(Protocol):
    def weights(self, panel: BarPanel) -> np.ndarray: ...


def with_garbage_after(panel: BarPanel, t: int, seed: int = 0) -> BarPanel:
    """``panel`` with every bar after ``t`` replaced by random (valid) bars."""
    rng = np.random.default_rng(seed)
    T, N = panel.shape
    k = T - t - 1
    kwargs = {f.name: getattr(panel, f.name) for f in fields(panel)}
    if k <= 0:
        return panel
    a = rng.integers(1_000_000, 10_000_000_000, size=(k, N))
    b = rng.integers(1_000_000, 10_000_000_000, size=(k, N))
    garbage = {"open": a, "close": b, "high": np.maximum(a, b) + 1, "low": np.minimum(a, b) - 1,
               "volume": rng.integers(0, 10**9, size=(k, N)), "dividend": rng.integers(0, 10**8, size=(k, N)),
               "split_num": rng.integers(1, 4, size=(k, N)), "split_den": rng.integers(1, 4, size=(k, N))}
    for name in ARRAYS:
        kwargs[name] = np.concatenate([getattr(panel, name)[: t + 1], garbage[name].astype(np.int64)])
    kwargs["valid"] = np.concatenate([panel.valid[: t + 1], np.ones((k, N), bool)])
    return BarPanel(**kwargs)


def _first_difference(a: np.ndarray, b: np.ndarray) -> int | None:
    same = (a == b) | (np.isnan(a) & np.isnan(b))
    bad = np.flatnonzero(~same.all(axis=1))
    return int(bad[0]) if len(bad) else None


def check_causal(strategy: BarStrategy, panel: BarPanel, cut_points: Iterable[int]) -> None:
    T = panel.shape[0]
    full = np.asarray(strategy.weights(panel), dtype=float)
    for t in cut_points:
        if not 0 <= t < T:
            continue
        for label, view in (("removed", panel.upto(t)), ("replaced with garbage", with_garbage_after(panel, t, t))):
            w = np.asarray(strategy.weights(view), dtype=float)[: t + 1]
            bar = _first_difference(w, full[: t + 1])
            if bar is not None:
                raise LookAheadError(f"{type(strategy).__name__}: weight at bar {bar} changes when the data "
                                     f"after bar {t} is {label}")
