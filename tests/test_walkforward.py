"""Walk-forward validation (M8): parameters are chosen on data before each test window,
and only out-of-sample results exist."""

import dataclasses

import numpy as np
import pytest

from research.backtest.bars import BarPanel
from research.backtest.metrics import sharpe
from research.backtest.vectorized import run_vectorized
from research.backtest.walkforward import FoldResult, WalkForwardResult, make_folds, walk_forward
from research.sim.bars import generate_bars


def test_rolling_folds() -> None:
    folds = make_folds(100, train=40, test=20)
    assert [(f.train_start, f.train_end, f.test_start, f.test_end) for f in folds] == [
        (0, 40, 40, 60), (20, 60, 60, 80), (40, 80, 80, 100)]


def test_anchored_folds() -> None:
    folds = make_folds(100, train=40, test=20, anchored=True)
    assert [(f.train_start, f.train_end) for f in folds] == [(0, 40), (0, 60), (0, 80)]


def test_folds_never_overlap_their_tests() -> None:
    for kwargs in ({"train": 50, "test": 10}, {"train": 30, "test": 25, "anchored": True}):
        folds = make_folds(200, **kwargs)
        for f in folds:
            assert f.train_start < f.train_end <= f.test_start < f.test_end <= 200
        for a, b in zip(folds, folds[1:]):
            assert a.test_end <= b.test_start


def test_too_little_data_is_an_error() -> None:
    with pytest.raises(ValueError):
        make_folds(50, train=40, test=20)


@pytest.fixture(scope="module")
def noise_panel() -> BarPanel:
    return BarPanel.from_frame(generate_bars(seed=9, symbols=("A", "B", "C", "D"), n_days=600,
                                             start="2015-01-02", drift=0.0))


class Spy:
    """Records how much history each weights() call was given."""
    seen: list[tuple[dict, int]] = []

    def __init__(self, **params) -> None:
        self.params = params

    def weights(self, panel: BarPanel) -> np.ndarray:
        Spy.seen.append((self.params, len(panel.dates)))
        return np.zeros(panel.close.shape)


def test_selection_sees_only_training_data(noise_panel) -> None:
    Spy.seen = []
    grid = [{"k": 1}, {"k": 2}, {"k": 3}]
    folds = make_folds(len(noise_panel.dates), train=200, test=100)
    walk_forward(noise_panel, Spy, grid, folds, capital=1_000_000)
    calls = iter(Spy.seen)
    for f in folds:
        for _ in grid:
            _, n = next(calls)
            assert n == f.train_end  # history ends at the last training bar
        _, n = next(calls)
        assert n == f.test_end
    assert next(calls, None) is None


def test_results_hold_no_in_sample_numbers() -> None:
    assert {f.name for f in dataclasses.fields(FoldResult)} == {"fold", "params"}
    names = {f.name for f in dataclasses.fields(WalkForwardResult)}
    # M9 adds the out-of-sample runs themselves (gross and net): still nothing in-sample.
    assert names == {"folds", "oos_dates", "oos_pnl", "oos_equity", "oos_metrics", "capital", "oos_runs"}


class RandomWeights:
    """Random positions from a seed: pure noise, causal (the first t draws do not
    depend on how long the panel is)."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def weights(self, panel: BarPanel) -> np.ndarray:
        rng = np.random.default_rng(self.seed)
        T, N = panel.close.shape
        return np.stack([rng.choice([-0.25, 0.25], size=N) for _ in range(T)])


def test_an_overfit_parameter_has_no_out_of_sample_edge() -> None:
    # 40 pure-noise strategies: in each training window the best of them looks good by
    # luck alone (selection bias). Out of sample that luck is gone. With 1750
    # out-of-sample bars the Sharpe ratio's standard error is about sqrt(252 / 1750) =
    # 0.38, so noise stays below 3 standard errors.
    panel = BarPanel.from_frame(generate_bars(seed=10, symbols=("A", "B", "C", "D"), n_days=2000,
                                              start="2010-01-04"))
    grid = [{"seed": s} for s in range(40)]
    folds = make_folds(len(panel.dates), train=250, test=250)
    capital = 10_000_000_000
    in_sample = []
    for f in folds:
        train = panel.upto(f.train_end - 1)
        in_sample.append(max(sharpe(run_vectorized(train, RandomWeights(**g).weights(train), capital=capital,
                                                   start=f.train_start, end=f.train_end).returns) for g in grid))
    assert np.mean(in_sample) > 1.5
    result = walk_forward(panel, RandomWeights, grid, folds, capital=capital)
    se = np.sqrt(252 / len(result.oos_pnl))
    assert len(result.oos_pnl) == 1750
    assert abs(result.oos_metrics["sharpe"]) < 3 * se < np.mean(in_sample)


def test_walk_forward_is_deterministic(noise_panel) -> None:
    grid = [{"seed": s} for s in range(5)]
    folds = make_folds(len(noise_panel.dates), train=200, test=100)
    a = walk_forward(noise_panel, RandomWeights, grid, folds, capital=10**9)
    b = walk_forward(noise_panel, RandomWeights, grid, folds, capital=10**9)
    assert a.oos_pnl.tolist() == b.oos_pnl.tolist()
    assert [f.params for f in a.folds] == [f.params for f in b.folds]
