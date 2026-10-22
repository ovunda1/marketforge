"""The three SPEC momentum strategies (M8): time-series and cross-sectional momentum on
bars, order-book momentum in replay."""

import numpy as np
import pandas as pd
import pytest

from research.backtest.bars import BarPanel
from research.backtest.causality import check_causal
from research.backtest.event import EventConfig, run_event_backtest
from research.data import schema
from research.sim.bars import generate_bars
from research.strategies.obmom import OrderBookMomentum
from research.strategies.tsmom import TimeSeriesMomentum
from research.strategies.xsmom import CrossSectionalMomentum


def panel_from_closes(closes: dict[str, list[int]], dates) -> BarPanel:
    rows = [{"symbol": s, "date": d, "open": c, "high": c, "low": c, "close": c}
            for s, cs in closes.items() for d, c in zip(dates, cs)]
    return BarPanel.from_frame(schema.normalize_bars_frame(pd.DataFrame(rows)))


# --- Time-series momentum -------------------------------------------------------------------

def expected_tsmom(c: np.ndarray, L: int, V: int, tv: float, cap: float) -> np.ndarray:
    """The rule written out longhand: sign of the trailing L-bar return, scaled to a
    target volatility by the trailing V daily returns, capped."""
    out = np.zeros(len(c))
    dr = c[1:] / c[:-1] - 1  # dr[k] is the return of bar k + 1
    for t in range(max(L, V), len(c)):
        vol = np.std(dr[t - V:t], ddof=1) * np.sqrt(252)
        out[t] = np.sign(c[t] / c[t - L] - 1) * min(tv / vol, cap)
    return out


def test_tsmom_matches_the_rule() -> None:
    closes = [100, 102, 99, 103, 101, 104, 100, 97, 99, 95]
    p = panel_from_closes({"A": closes}, pd.bdate_range("2020-01-01", periods=10))
    w = TimeSeriesMomentum(lookback=3, vol_window=4, target_vol=0.1, max_leverage=2.0).weights(p)
    np.testing.assert_allclose(w[:, 0], expected_tsmom(np.array(closes, float), 3, 4, 0.1, 2.0))
    assert (w[:4] == 0).all() and (w[4:] != 0).all()
    assert np.sign(w[9, 0]) == -1  # 95 < 101: short


def test_tsmom_caps_leverage() -> None:
    closes = [100, 101, 102, 103, 104, 105, 106]  # almost no volatility
    p = panel_from_closes({"A": closes}, pd.bdate_range("2020-01-01", periods=7))
    w = TimeSeriesMomentum(lookback=3, vol_window=3, target_vol=0.1, max_leverage=2.0).weights(p)
    assert w[3:, 0].tolist() == [2.0] * 4


def test_tsmom_splits_risk_across_symbols_with_a_signal() -> None:
    a = [100, 102, 99, 103, 101, 104, 100, 97]
    p = panel_from_closes({"A": a, "B": a[::-1]}, pd.bdate_range("2020-01-01", periods=8))
    w = TimeSeriesMomentum(lookback=3, vol_window=4, target_vol=0.1, max_leverage=2.0).weights(p)
    np.testing.assert_allclose(w[:, 0], expected_tsmom(np.array(a, float), 3, 4, 0.1, 2.0) / 2)


def test_tsmom_uses_total_returns() -> None:
    # A 2:1 split halves the price; that is not a crash.
    rows = [{"symbol": "A", "date": d, "open": c, "high": c, "low": c, "close": c,
             "split_num": s, "split_den": 1}
            for d, c, s in zip(pd.bdate_range("2020-01-01", periods=6),
                               [100, 101, 102, 52, 53, 54], [1, 1, 1, 2, 1, 1])]
    p = BarPanel.from_frame(schema.normalize_bars_frame(pd.DataFrame(rows)))
    w = TimeSeriesMomentum(lookback=3, vol_window=3, target_vol=0.1, max_leverage=2.0).weights(p)
    assert (w[3:, 0] > 0).all()


# --- Cross-sectional momentum -----------------------------------------------------------------

MONTHLY = pd.to_datetime([f"2020-{m:02d}-{d:02d}" for m in range(1, 7) for d in (2, 20)])


def xs_panel(final: dict[str, int]) -> BarPanel:
    # Flat at 100 until early February's second bar, then each symbol jumps to its level.
    return panel_from_closes({s: [100] * 4 + [v] * 8 for s, v in final.items()}, MONTHLY)


def test_xsmom_ranks_12_minus_1_and_rebalances_at_month_starts() -> None:
    p = xs_panel({"A": 130, "B": 110, "C": 95, "D": 80})
    w = CrossSectionalMomentum(lookback_months=3, skip_months=1, quantile=0.25).weights(p)
    # Month ends are bars 1, 3, 5, 7, 9, 11, and a month end is only known on the first
    # bar of the next month. At bar 8 (May 2): the return from the January end (bar 1)
    # to the March end (bar 5), skipping April.
    assert (w[:8] == 0).all()
    for t in (8, 9, 10, 11):
        assert w[t].tolist() == [0.5, 0.0, 0.0, -0.5]


def test_xsmom_skips_the_most_recent_month() -> None:
    # D only collapses in April (bars 6-7): the March-end signal at bar 8 cannot see it.
    closes = {"A": [100] * 4 + [130] * 8, "B": [100] * 4 + [110] * 8, "C": [100] * 4 + [95] * 8,
              "D": [100] * 6 + [50] * 6}
    w = CrossSectionalMomentum(lookback_months=3, skip_months=1, quantile=0.25).weights(
        panel_from_closes(closes, MONTHLY))
    assert w[8].tolist() == [0.5, 0.0, -0.5, 0.0]


def test_xsmom_breaks_ties_by_symbol() -> None:
    p = xs_panel({"A": 130, "B": 130, "C": 80, "D": 80})
    w = CrossSectionalMomentum(lookback_months=3, skip_months=1, quantile=0.25).weights(p)
    assert w[8].tolist() == [0.5, 0.0, 0.0, -0.5]


def test_xsmom_is_dollar_neutral() -> None:
    p = BarPanel.from_frame(generate_bars(seed=1, symbols=tuple("ABCDEFGHIJ"), n_days=700, start="2015-01-02"))
    w = CrossSectionalMomentum(lookback_months=12, skip_months=1, quantile=0.2).weights(p)
    active = np.abs(w).sum(axis=1) > 0
    assert active.any()
    np.testing.assert_allclose(w[active].sum(axis=1), 0, atol=1e-12)
    np.testing.assert_allclose(np.abs(w[active]).sum(axis=1), 1.0)


# --- Causality of the bar strategies ----------------------------------------------------------------

@pytest.mark.parametrize("strategy", [
    TimeSeriesMomentum(lookback=60, vol_window=20, target_vol=0.1, max_leverage=2.0),
    CrossSectionalMomentum(lookback_months=12, skip_months=1, quantile=0.2),
], ids=["tsmom", "xsmom"])
def test_bar_strategies_are_causal(strategy) -> None:
    p = BarPanel.from_frame(generate_bars(seed=2, symbols=tuple("ABCDEF"), n_days=600, start="2015-01-02",
                                          split_every=170, dividend_every=63))
    check_causal(strategy, p, cut_points=range(20, 600, 37))


# --- Order-book momentum ------------------------------------------------------------------------

def test_obmom_score() -> None:
    s = OrderBookMomentum(horizon_ns=10, return_weight=1.0, imbalance_weight=2.0, threshold=1.0, qty=1,
                          max_position=1)
    assert s.score(101.0, 100.0, 30, 10) == pytest.approx(100.0 + 2.0 * 0.5)
    assert s.score(100.0, 100.0, 10, 30) == pytest.approx(-1.0)


def test_obmom_buys_a_rising_bid_heavy_book_and_flattens() -> None:
    ms = 1_000_000
    rows = [(i * ms, 100 + i // 20, 50, 101 + i // 20, 10) for i in range(300)]
    quotes = pd.DataFrame(rows, columns=["ts", "bid_px", "bid_qty", "ask_px", "ask_qty"]).astype(
        schema.COLUMNS["quotes"])
    trades = pd.DataFrame(columns=["ts", "trade_id", "price", "qty", "aggressor"]).astype(schema.COLUMNS["trades"])
    strategy = OrderBookMomentum(horizon_ns=10 * ms, return_weight=1.0, imbalance_weight=10.0, threshold=5.0,
                                 qty=5, max_position=10, flatten_before_end_ns=50 * ms)
    result = run_event_backtest(quotes, trades, strategy, EventConfig(latency_ns=ms))
    buys = [f for f in result.fills if f.side.name == "BUY"]
    assert sum(f.qty for f in buys) >= 10
    assert max(f.ts for f in buys) < 250 * ms  # no new risk in the flatten window
    assert result.position == 0
