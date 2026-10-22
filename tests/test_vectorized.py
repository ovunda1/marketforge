"""Vectorized bar backtester (M8): signals at the close of t, trades at t+lag (lag >= 1),
integer shares and integer P&L, splits and dividends point-in-time, a cost-model hook."""

from collections.abc import Sequence

import numpy as np
import pandas as pd
import pytest

from research.backtest.bars import BarPanel
from research.backtest.causality import check_causal
from research.backtest.costs import CostModel, TradeRecord, ZeroCost
from research.backtest.strategy import LookAheadError
from research.backtest.vectorized import BarExecution, run_vectorized
from research.data import schema


def panel(open_: list[list[int]], close: list[list[int]], *, dividend=None, split=None,
          symbols=("A",), start="2020-01-01") -> BarPanel:
    """Columns per symbol; rows are days. high/low are derived so the bars are valid."""
    o, c = np.array(open_), np.array(close)
    T, N = c.shape
    dates = pd.bdate_range(start, periods=T)
    rows = []
    for j, sym in enumerate(symbols):
        for t in range(T):
            num, den = split[t][j] if split else (1, 1)
            rows.append({"symbol": sym, "date": dates[t], "open": o[t, j], "close": c[t, j],
                         "high": max(o[t, j], c[t, j]), "low": min(o[t, j], c[t, j]), "volume": 1000,
                         "dividend": dividend[t][j] if dividend else 0, "split_num": num, "split_den": den})
    return BarPanel.from_frame(schema.normalize_bars_frame(pd.DataFrame(rows)))


def col(values: list[int]) -> list[list[int]]:
    return [[v] for v in values]


P = panel(col([10, 11, 12, 13, 14]), col([10, 12, 11, 14, 15]))
W = np.array([[1.0], [1.0], [0.0], [0.0], [0.0]])


def test_signal_at_close_trades_at_next_open() -> None:
    r = run_vectorized(P, W, capital=100)
    # Buy 10 at the open of day 1 (11), sell 10 at the open of day 3 (13).
    assert [(t.bar, t.qty, t.price) for t in r.trades] == [(1, 10, 11), (3, -10, 13)]
    assert r.equity.tolist() == [100, 110, 100, 120, 120]
    assert r.pnl.tolist() == [0, 10, -10, 20, 0]
    assert r.positions[:, 0].tolist() == [0, 10, 10, 0, 0]
    assert r.equity.dtype == np.int64 and r.positions.dtype == np.int64


def test_fill_at_the_next_close() -> None:
    r = run_vectorized(P, W, capital=100, execution=BarExecution(price="close"))
    assert [(t.bar, t.qty, t.price) for t in r.trades] == [(1, 10, 12), (3, -10, 14)]
    assert r.equity.tolist() == [100, 100, 90, 120, 120]


def test_longer_lag() -> None:
    r = run_vectorized(P, W, capital=100, execution=BarExecution(lag=2))
    assert [(t.bar, t.price) for t in r.trades] == [(2, 12), (4, 14)]


def test_same_bar_execution_is_impossible() -> None:
    with pytest.raises(ValueError, match="lag"):
        BarExecution(lag=0)
    with pytest.raises(ValueError, match="price"):
        BarExecution(price="vwap")


def test_shares_are_truncated_toward_zero() -> None:
    w = np.array([[0.55], [0.55], [-0.55], [-0.55], [0.0]])
    r = run_vectorized(P, w, capital=100)
    # 0.55 * 100 / 10 = 5.5 -> 5; -0.55 * 100 / 11 = -5 -> -5 (from 5: sell 10). The last
    # signal would trade after the data ends.
    assert [(t.bar, t.qty) for t in r.trades] == [(1, 5), (3, -10)]


def test_unchanged_weights_do_not_rebalance() -> None:
    # Constant weight while the price moves: shares are set when the weight changes.
    w = np.full((5, 1), 0.5)
    r = run_vectorized(P, w, capital=100)
    assert [(t.bar, t.qty) for t in r.trades] == [(1, 5)]


def test_a_split_scales_the_position() -> None:
    p = panel(col([10, 10, 5, 5]), col([10, 10, 5, 5]), dividend=col([0, 0, 0, 100]),
              split=[[(1, 1)], [(1, 1)], [(2, 1)], [(1, 1)]])
    r = run_vectorized(p, np.ones((4, 1)), capital=100)
    assert r.positions[:, 0].tolist() == [0, 10, 20, 20]
    assert len(r.trades) == 1
    # The ex-date dividend is paid on the shares held into it.
    assert r.equity.tolist() == [100, 100, 100, 120]


def test_a_reverse_split_pays_cash_in_lieu() -> None:
    p = panel(col([10, 10, 30]), col([10, 10, 30]), split=[[(1, 1)], [(1, 1)], [(1, 3)]])
    r = run_vectorized(p, np.ones((3, 1)), capital=100)
    # 10 old shares -> 3 new and 1/3 of a new share paid at the close (30 / 3 = 10).
    assert r.positions[:, 0].tolist() == [0, 10, 3]
    assert r.equity.tolist() == [100, 100, 100]


def test_short_positions() -> None:
    w = -W
    r = run_vectorized(P, w, capital=100)
    assert r.equity.tolist() == [100, 90, 100, 80, 80]


def test_a_symbol_trades_only_once_it_has_bars() -> None:
    rows = []
    dates = pd.bdate_range("2020-01-01", periods=4)
    for t in range(4):
        rows.append({"symbol": "A", "date": dates[t], "open": 10, "high": 10, "low": 10, "close": 10})
        if t >= 2:
            rows.append({"symbol": "B", "date": dates[t], "open": 20, "high": 20, "low": 20, "close": 20})
    df = schema.normalize_bars_frame(pd.DataFrame(rows))
    p = BarPanel.from_frame(df)
    assert p.valid.tolist() == [[True, False], [True, False], [True, True], [True, True]]
    w = np.array([[0.5, 0.5]] * 4)
    r = run_vectorized(p, w, capital=200)
    assert [(t.bar, t.symbol, t.qty) for t in r.trades] == [(1, 0, 10), (3, 1, 5)]


def test_evaluation_window_starts_flat() -> None:
    # The signal at the close of day 1 (sized at 12: 8 shares) is the first one that
    # trades inside the window, at the open of day 2 (12).
    r = run_vectorized(P, np.ones((5, 1)), capital=100, start=2, end=5)
    assert r.positions[:, 0].tolist() == [8, 8, 8]
    assert [(t.bar, t.qty, t.price) for t in r.trades] == [(2, 8, 12)]
    assert r.equity.tolist() == [4 + 8 * 11, 4 + 8 * 14, 4 + 8 * 15]


def test_bad_weights_are_refused() -> None:
    with pytest.raises(ValueError, match="shape"):
        run_vectorized(P, np.ones((4, 1)), capital=100)
    w = W.copy()
    w[0, 0] = np.inf
    with pytest.raises(ValueError, match="finite"):
        run_vectorized(P, w, capital=100)


# --- Costs ------------------------------------------------------------------------------------

class FixedPerTrade(CostModel):
    name = "fixed"

    def __init__(self, c: int) -> None:
        self.c = c

    def costs(self, trades: Sequence[TradeRecord]) -> list[int]:
        return [self.c] * len(trades)


def test_zero_cost_means_net_equals_gross() -> None:
    r = run_vectorized(P, W, capital=100, cost_model=ZeroCost())
    assert r.costs == 0 and r.net_pnl == r.gross_pnl == 20


def test_a_cost_model_reduces_net_pnl() -> None:
    r = run_vectorized(P, W, capital=100, cost_model=FixedPerTrade(3))
    assert r.gross_pnl == 20 and r.costs == 6 and r.net_pnl == 14
    assert r.net_equity.tolist() == [100, 107, 97, 114, 114]


# --- Panel and look-ahead -----------------------------------------------------------------------

def test_prefix_views_are_read_only_and_end_at_t() -> None:
    v = P.upto(2)
    assert len(v.dates) == 3 and v.close[:, 0].tolist() == [10, 12, 11]
    with pytest.raises(ValueError):
        v.close[0, 0] = 99


def test_total_return_index_is_point_in_time() -> None:
    p = panel(col([10, 10, 5, 5]), col([10, 10, 5, 5]), dividend=col([0, 0, 0, 100]),
              split=[[(1, 1)], [(1, 1)], [(2, 1)], [(1, 1)]])
    tr = p.total_return_index()
    np.testing.assert_allclose(tr[:, 0], [1.0, 1.0, 1.0, 1.2])
    np.testing.assert_allclose(p.upto(2).total_return_index(), tr[:3])


class Honest:
    def weights(self, panel: BarPanel) -> np.ndarray:
        c = panel.close.astype(float)
        out = np.zeros_like(c)
        out[1:] = np.sign(c[1:] - c[:-1])
        return out


class Cheater:
    def weights(self, panel: BarPanel) -> np.ndarray:
        c = panel.close.astype(float)
        out = np.zeros_like(c)
        out[:-1] = np.sign(c[1:] - c[:-1])  # tomorrow's move
        return out


class Normalizer:
    """Subtle look-ahead: scales by the full-sample volatility."""

    def weights(self, panel: BarPanel) -> np.ndarray:
        c = panel.close.astype(float)
        r = np.zeros_like(c)
        r[1:] = c[1:] / c[:-1] - 1
        return r / r.std()


def random_panel() -> BarPanel:
    rng = np.random.default_rng(0)
    close = (1000 + np.cumsum(rng.integers(-20, 21, size=(60, 2)), axis=0)).tolist()
    return panel(close, close, symbols=("A", "B"))


def test_causality_check_passes_honest_signals() -> None:
    check_causal(Honest(), random_panel(), cut_points=range(5, 60, 5))


@pytest.mark.parametrize("strategy", [Cheater(), Normalizer()])
def test_causality_check_catches_look_ahead(strategy) -> None:
    with pytest.raises(LookAheadError, match="bar"):
        check_causal(strategy, random_panel(), cut_points=range(5, 60, 5))
