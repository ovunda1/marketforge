"""Analytics (M9): every metric on a series small enough to check by hand, or against an
independent computation (``statistics``, a seeded bootstrap)."""

from __future__ import annotations

import math
import statistics

import numpy as np
import pandas as pd
import pytest

from research.analytics import metrics as m

R = np.array([0.01, 0.02, -0.01, 0.03])


def test_periods_per_year_from_daily_dates() -> None:
    assert m.periods_per_year(pd.bdate_range("2020-01-01", periods=30).values) == 252


def test_non_daily_bars_need_an_explicit_periods_per_year() -> None:
    with pytest.raises(ValueError, match="periods_per_year"):
        m.periods_per_year(pd.date_range("2020-01-03", periods=30, freq="W-FRI").values)


def test_annual_return_is_arithmetic() -> None:
    assert m.annual_return(R) == pytest.approx(0.0125 * 252)


def test_volatility_is_the_sample_std_annualized() -> None:
    assert m.volatility(R) == pytest.approx(statistics.stdev(R) * math.sqrt(252))


def test_sharpe_is_excess_mean_over_sample_std() -> None:
    assert m.sharpe(R) == pytest.approx(statistics.mean(R) / statistics.stdev(R) * math.sqrt(252))
    rf = np.full(4, 0.001)
    ex = R - rf
    assert m.sharpe(R, rf) == pytest.approx(statistics.mean(ex) / statistics.stdev(ex) * math.sqrt(252))


def test_sharpe_of_a_flat_series_is_zero() -> None:
    assert m.sharpe(np.zeros(10)) == 0.0


def test_sortino_uses_the_downside_deviation_over_all_periods() -> None:
    r = np.array([0.02, -0.01, 0.03, -0.02])
    downside = math.sqrt((0.01 ** 2 + 0.02 ** 2) / 4)
    assert m.sortino(r) == pytest.approx(0.005 / downside * math.sqrt(252))


def test_sortino_without_losses_is_undefined() -> None:
    assert m.sortino(np.array([0.01, 0.02])) is None


def test_drawdown_depth_and_duration() -> None:
    d = m.drawdown(np.array([100, 110, 105, 90, 95, 112, 100]))
    assert d.max_drawdown == pytest.approx(20 / 110)
    assert (d.peak, d.trough, d.recovery, d.duration, d.recovered) == (1, 3, 5, 4, True)


def test_an_unrecovered_drawdown_runs_to_the_end() -> None:
    d = m.drawdown(np.array([100, 120, 90, 100]))
    assert (d.peak, d.trough, d.recovery, d.duration, d.recovered) == (1, 2, None, 2, False)
    assert m.drawdown(np.array([100, 101, 102])).max_drawdown == 0.0


def test_calmar() -> None:
    r = np.array([0.1, -0.2, 0.05])
    equity = 1 + np.concatenate([[0], np.cumsum(r)])
    assert m.calmar(r, equity) == pytest.approx(m.annual_return(r) / (0.2 / 1.1))
    assert m.calmar(np.array([0.01, 0.01]), np.array([1, 1.01, 1.02])) is None


def test_beta_and_alpha_to_a_benchmark() -> None:
    b = np.random.default_rng(1).normal(0, 0.01, 500)
    out = m.beta(2 * b + 0.001, b)
    assert out["beta"] == pytest.approx(2.0)
    assert out["alpha_annual"] == pytest.approx(0.001 * 252)
    assert out["correlation"] == pytest.approx(1.0)


def test_beta_ignores_periods_where_either_side_is_missing() -> None:
    b = np.array([0.01, np.nan, -0.02, 0.03, 0.0])
    r = 3 * np.nan_to_num(b)
    assert m.beta(r, b)["n"] == 4 and m.beta(r, b)["beta"] == pytest.approx(3.0)


# --- Sharpe uncertainty ----------------------------------------------------------------------------

def test_sharpe_standard_error_iid() -> None:
    r = np.random.default_rng(2).normal(0.0005, 0.01, 1000)
    sr = r.mean() / r.std(ddof=1)
    assert m.sharpe_se(r)["iid"] == pytest.approx(math.sqrt((1 + sr ** 2 / 2) / 1000) * math.sqrt(252))


def test_sharpe_standard_error_matches_a_bootstrap_for_skewed_returns() -> None:
    rng = np.random.default_rng(3)
    r = rng.lognormal(0, 0.8, 2000) * 0.01 - 0.012   # strongly right-skewed
    boot = []
    for _ in range(2000):
        s = r[rng.integers(0, len(r), len(r))]
        boot.append(s.mean() / s.std(ddof=1) * math.sqrt(252))
    se = m.sharpe_se(r)
    assert se["mertens"] == pytest.approx(float(np.std(boot)), rel=0.15)
    assert se["mertens"] != pytest.approx(se["iid"], rel=0.05)  # skew and kurtosis matter here


def test_probabilistic_sharpe() -> None:
    r = np.random.default_rng(5).normal(0.001, 0.01, 750)
    sr = r.mean() / r.std(ddof=1)
    g3 = float(((r - r.mean()) ** 3).mean() / r.std() ** 3)
    g4 = float(((r - r.mean()) ** 4).mean() / r.std() ** 4)
    z = sr * math.sqrt(len(r) - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    assert m.psr(r) == pytest.approx(statistics.NormalDist().cdf(z))
    assert m.psr(np.array([0.01, -0.01] * 100)) == pytest.approx(0.5)


def test_deflated_sharpe_falls_with_the_number_of_trials() -> None:
    r = np.random.default_rng(6).normal(0.001, 0.01, 750)
    one = m.dsr(r, 1)
    assert one["sr0_annual"] == 0.0 and one["probability"] == pytest.approx(m.psr(r))
    probs = [m.dsr(r, n)["probability"] for n in (1, 2, 6, 50)]
    assert probs == sorted(probs, reverse=True) and probs[-1] < probs[0]


# --- Trades ---------------------------------------------------------------------------------------------

def test_episodes_split_flips_and_allocate_costs() -> None:
    held = np.array([[0], [10], [10], [-5], [-5], [0]])
    pos = np.array([[10], [10], [-5], [-5], [0], [0]])
    old = np.array([[0], [3], [2], [-4], [1], [0]])
    new = np.array([[5], [0], [-1], [0], [0], [0]])
    costs = np.array([[1], [0], [3], [0], [1], [0]])
    eps, still_open = m.episodes(held, pos, old, new, costs)
    assert still_open == 0
    assert [(e.symbol, e.start, e.end, e.gross, e.net) for e in eps] == [(0, 0, 2, 10, 7), (0, 2, 4, -4, -6)]
    # The flip's 3 of cost splits by size: 10 shares closed, 5 opened.
    assert [e.duration for e in eps] == [2, 2]


def test_an_episode_still_open_at_the_end_is_counted_separately() -> None:
    eps, still_open = m.episodes(np.array([[0], [1]]), np.array([[1], [1]]), np.zeros((2, 1)), np.zeros((2, 1)),
                                 np.zeros((2, 1)))
    assert eps == [] and still_open == 1


def test_win_statistics() -> None:
    s = m.win_stats([7, -6, 2, 0])
    assert s["n"] == 4 and s["hit_rate"] == pytest.approx(0.5)
    assert s["avg_win"] == pytest.approx(4.5) and s["avg_loss"] == pytest.approx(-6)
    assert s["profit_factor"] == pytest.approx(9 / 6)
    assert m.win_stats([1, 2])["profit_factor"] is None
    assert m.win_stats([])["hit_rate"] is None


@pytest.mark.parametrize(("trades", "expected"), [
    ([(0, 0, 100), (2, 0, -50), (4, 0, -50)], 3.0),
    ([(0, 0, -100), (3, 0, 100)], 3.0),
    # A flip closes the long (100 shares, 2 bars) and opens a short (50 shares, 2 bars).
    ([(0, 0, 100), (2, 0, -150), (4, 0, 50)], 2.0),
])
def test_fifo_holding_period(trades, expected) -> None:
    assert m.holding_period(trades, np.ones((5, 1))) == pytest.approx(expected)


def test_holding_period_follows_splits() -> None:
    split = np.ones((5, 1))
    split[1, 0] = 2.0
    assert m.holding_period([(0, 0, 100), (2, 0, -100), (4, 0, -100)], split) == pytest.approx(3.0)


def test_turnover_and_exposure() -> None:
    assert m.turnover(2_000_000, 1_000_000, n=252) == pytest.approx(2.0)
    e = m.exposure(np.array([0, 500_000, 1_000_000]), np.array([0, -500_000, 1_000_000]), 1_000_000)
    assert e == pytest.approx({"gross": 0.5, "net": 500_000 / 3 / 1_000_000, "time_in_market": 2 / 3})


def test_cost_drag_and_break_even() -> None:
    out = m.cost_drag(gross_excess=np.full(252, 0.0004), costs=np.full(252, 100), capital=1_000_000)
    assert out["annual_cost"] == pytest.approx(100 * 252 / 1_000_000)
    assert out["gross_excess_annual"] == pytest.approx(0.0004 * 252)
    assert out["break_even_multiple"] == pytest.approx(0.0004 / 0.0001)
    assert m.cost_drag(np.full(5, 0.001), np.zeros(5), 1)["break_even_multiple"] is None
