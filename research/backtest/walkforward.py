"""Walk-forward validation (M8; net selection M9): the only way sweeps report results.

For each fold, every parameter set in the grid is backtested on the training window and
the best (by net excess Sharpe: after the cost model, over the risk-free rate; the first
in grid order on a tie) is then run on the test window that follows. Costs are known
before trading, so choosing on gross would pick strategies the costs then ruin. The selection step is handed ``panel.upto(train_end - 1)``: the bars after
training do not exist for it. The test run gets ``panel.upto(test_end - 1)``. Each window
starts flat.

The result holds the chosen parameters and the out-of-sample runs (gross and net), and
nothing else: in-sample scores are not part of it, so they cannot be reported.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from research.analytics.metrics import sharpe
from research.backtest.bars import BarPanel
from research.backtest.costs import CostModel
from research.backtest.metrics import summary
from research.backtest.rates import RiskFree
from research.backtest.vectorized import BarExecution, BarResult, run_vectorized


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
    oos_runs: tuple[BarResult, ...] = ()   # each test window's run, gross and net


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
                 capital: int, execution: BarExecution = BarExecution(), cost_model: CostModel | None = None, *,
                 risk_free: RiskFree | None = None, liquidate_at_end: bool = True) -> WalkForwardResult:
    if not grid:
        raise ValueError("empty parameter grid")
    chosen, runs = [], []

    def run(p: BarPanel, params: dict[str, Any], start: int, end: int) -> BarResult:
        return run_vectorized(p, factory(**params).weights(p), capital, execution, cost_model, start, end,
                              risk_free=risk_free, liquidate_at_end=liquidate_at_end)

    for fold in folds:
        train_panel = panel.upto(fold.train_end - 1)
        best_score, best = -np.inf, grid[0]
        for params in grid:
            r = run(train_panel, params, fold.train_start, fold.train_end)
            score = sharpe(r.returns, r.rf_returns)
            if score > best_score:
                best_score, best = score, params
        r = run(panel.upto(fold.test_end - 1), best, fold.test_start, fold.test_end)
        chosen.append(FoldResult(fold, dict(best)))
        runs.append(r)
    oos_pnl = np.concatenate([r.net_pnl_per_bar for r in runs])
    return WalkForwardResult(folds=tuple(chosen), oos_dates=np.concatenate([r.dates for r in runs]), oos_pnl=oos_pnl,
                             oos_equity=capital + np.cumsum(oos_pnl), oos_metrics=summary(oos_pnl, capital),
                             capital=capital, oos_runs=tuple(runs))
