"""Walk-forward validation (M8): the only way sweeps report results.

For each fold, every parameter set in the grid is backtested on the training window and
the best (by Sharpe; the first in grid order on a tie) is then run on the test window
that follows. The selection step is handed ``panel.upto(train_end - 1)``: the bars after
training do not exist for it. The test run gets ``panel.upto(test_end - 1)``. Each window
starts flat.

The result holds the chosen parameters and the concatenated out-of-sample P&L, and
nothing else: in-sample scores are not part of it, so they cannot be reported.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from research.backtest.bars import BarPanel
from research.backtest.costs import CostModel, ZeroCost
from research.backtest.metrics import sharpe, summary
from research.backtest.vectorized import BarExecution, run_vectorized


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class FoldResult:
    fold: Fold
    params: dict[str, Any]


@dataclass(frozen=True)
class WalkForwardResult:
    folds: tuple[FoldResult, ...]
    oos_dates: np.ndarray
    oos_pnl: np.ndarray       # int64, net, per bar
    oos_equity: np.ndarray    # int64
    oos_metrics: dict[str, float]
    capital: int


def make_folds(n_bars: int, train: int, test: int, step: int | None = None, anchored: bool = False) -> list[Fold]:
    """Consecutive test windows of ``test`` bars, each after ``train`` training bars
    (anchored: all bars since the start). Windows advance by ``step`` (default ``test``)."""
    step = test if step is None else step
    if train < 1 or test < 1 or step < test:
        raise ValueError("train and test must be >= 1, and step >= test so test windows do not overlap")
    folds = []
    k = 0
    while True:
        train_end = train + k * step
        test_end = train_end + test
        if test_end > n_bars:
            break
        folds.append(Fold(0 if anchored else k * step, train_end, train_end, test_end))
        k += 1
    if not folds:
        raise ValueError(f"{n_bars} bars are not enough for one fold of {train} + {test}")
    return folds


def walk_forward(panel: BarPanel, factory: Callable[..., Any], grid: Sequence[dict[str, Any]], folds: Sequence[Fold],
                 capital: int, execution: BarExecution = BarExecution(), cost_model: CostModel = ZeroCost()
                 ) -> WalkForwardResult:
    if not grid:
        raise ValueError("empty parameter grid")
    chosen, pnl, dates = [], [], []
    for fold in folds:
        train_panel = panel.upto(fold.train_end - 1)
        best_score, best = -np.inf, grid[0]
        for params in grid:
            w = factory(**params).weights(train_panel)
            r = run_vectorized(train_panel, w, capital, execution, cost_model, fold.train_start, fold.train_end)
            score = sharpe(r.returns)
            if score > best_score:
                best_score, best = score, params
        test_panel = panel.upto(fold.test_end - 1)
        w = factory(**best).weights(test_panel)
        r = run_vectorized(test_panel, w, capital, execution, cost_model, fold.test_start, fold.test_end)
        chosen.append(FoldResult(fold, dict(best)))
        pnl.append(r.net_pnl_per_bar)
        dates.append(r.dates)
    oos_pnl = np.concatenate(pnl)
    return WalkForwardResult(folds=tuple(chosen), oos_dates=np.concatenate(dates), oos_pnl=oos_pnl,
                             oos_equity=capital + np.cumsum(oos_pnl), oos_metrics=summary(oos_pnl, capital),
                             capital=capital)
