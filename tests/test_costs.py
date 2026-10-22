"""Cost models (M9): each component on its own, the modes it may be used in, point-in-time
impact inputs, and the bar backtester charging them (with financing and the window-end
liquidation)."""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pandas as pd
import pytest

from research.backtest.bars import BarPanel
from research.backtest.costs import (
    CostContext, CostInputs, CostModel, Financing, TradeRecord, build_cost_model, load_schedule,
)
from research.backtest.rates import RiskFree
from research.backtest.vectorized import BarExecution, run_vectorized
from research.backtest.walkforward import make_folds, walk_forward
from research.data import schema

MICRO = "0.000001"
DOLLAR = 1_000_000  # micro-dollars


def ctx(*, vol=0.02, adv=100_000_000 * DOLLAR, n_obs=20, dates=("2024-06-03",), unit=MICRO, mode="bar",
        min_periods=5, fallback_bps=50) -> CostContext:
    T = len(dates)
    inputs = CostInputs(vol=np.full((T, 1), vol), adv=np.full((T, 1), float(adv)), n_obs=np.full((T, 1), n_obs))
    return CostContext(mode=mode, money_unit=Fraction(unit), dates=np.array(dates, dtype="datetime64[D]"),
                       inputs=inputs if mode == "bar" else None, min_periods=min_periods, fallback_bps=fallback_bps)


def buy(qty: int, price: int = 10 * DOLLAR, **kw) -> TradeRecord:
    return TradeRecord(symbol=0, qty=qty, price=price, bar=kw.pop("bar", 0), **kw)


def charge(section: dict, trades: list[TradeRecord], c: CostContext | None = None, mode: str = "bar"):
    return build_cost_model(section, mode).charge(trades, c or ctx())


# --- Components -----------------------------------------------------------------------------------

def test_no_components_charge_nothing() -> None:
    ch = CostModel().charge([buy(100)], ctx())
    assert ch.components == {} and ch.total.tolist() == [0]


def test_commission_per_share_is_exact() -> None:
    ch = charge({"commission": {"per_share": "0.0035"}}, [buy(1000), buy(-1000)])
    assert ch.components["commission"].tolist() == [3_500_000, 3_500_000]  # $3.50


def test_bps_fees_round_up() -> None:
    # 100.0001 micro-dollars of fee is charged as 101: a cost is never rounded down.
    ch = charge({"commission": {"bps": 1.0}}, [buy(1, price=1_000_001)])
    assert ch.components["commission"].tolist() == [101]


def test_maker_taker_by_liquidity_flag() -> None:
    c = ctx(mode="event", unit="0.01")
    section = {"maker_taker": {"maker_per_share": "-0.0020", "taker_per_share": "0.0030"}}
    trades = [TradeRecord(symbol=0, qty=100, price=10_000, ts=0, liquidity="TAKER"),
              TradeRecord(symbol=0, qty=-100, price=10_000, ts=0, liquidity="MAKER"),
              # 3 x -0.0025 = -0.0075 dollars = -0.75 ticks: a rebate rounds toward zero.
              TradeRecord(symbol=0, qty=3, price=10_000, ts=0, liquidity="MAKER")]
    ch = build_cost_model({"maker_taker": {"maker_per_share": "-0.0025", "taker_per_share": "0.0030"}},
                          "event").charge(trades[2:], c)
    assert ch.components["maker_taker"].tolist() == [0]
    ch = build_cost_model(section, "event").charge(trades[:2], c)
    assert ch.components["maker_taker"].tolist() == [30, -20]


def test_bar_fills_pay_the_taker_fee() -> None:
    ch = charge({"maker_taker": {"maker_per_share": "-0.0020", "taker_per_share": "0.0030"}}, [buy(100)])
    assert ch.components["maker_taker"].tolist() == [300_000]  # $0.30


SCHEDULE = [{"from": "2020-02-18", "rate_per_million": "22.10"}, {"from": "2021-02-25", "rate_per_million": "5.10"}]


def test_sec_fee_uses_the_rate_in_force_on_the_trade_date() -> None:
    c = ctx(dates=("2021-02-24", "2021-02-25"))
    sell = [TradeRecord(symbol=0, qty=-100_000, price=10 * DOLLAR, bar=b) for b in (0, 1)]  # $1M each
    ch = charge({"sec_fee": {"schedule": SCHEDULE}}, sell, c)
    assert ch.components["sec_fee"].tolist() == [22_100_000, 5_100_000]
    assert charge({"sec_fee": {"schedule": SCHEDULE}}, [buy(100_000)], c).components["sec_fee"].tolist() == [0]


def test_sec_fee_before_its_schedule_is_an_error() -> None:
    with pytest.raises(ValueError, match="no SEC fee rate"):
        charge({"sec_fee": {"schedule": SCHEDULE}}, [buy(-1)], ctx(dates=("2019-01-02",)))


def test_the_built_in_sec31_schedule_is_dated_and_sourced() -> None:
    rows = load_schedule("sec31")
    assert [r["from"] for r in rows] == sorted(r["from"] for r in rows)
    assert {"from": "2024-05-22", "rate_per_million": "27.80"}.items() <= next(
        r for r in rows if r["from"] == "2024-05-22").items()
    assert all(r["source"].startswith("https://www.sec.gov/") for r in rows)
    assert rows[0]["from"] <= "2007-01-03"


def test_event_sec_fee_dates_come_from_the_timestamp() -> None:
    c = ctx(mode="event", unit="0.01")
    ts = int(pd.Timestamp("2021-02-25T15:00:00Z").value)
    t = TradeRecord(symbol=0, qty=-100_000, price=1_000, ts=ts, liquidity="TAKER")  # $1M
    ch = build_cost_model({"sec_fee": {"schedule": SCHEDULE}}, "event").charge([t], c)
    assert ch.components["sec_fee"].tolist() == [510]  # $5.10 in cents


def test_spread_is_half_the_spread_on_each_side() -> None:
    ch = charge({"spread": {"half_spread_bps": 2.0}}, [buy(100_000), buy(-100_000)])
    assert ch.components["spread"].tolist() == [200 * DOLLAR, 200 * DOLLAR]


def test_fixed_slippage_is_linear() -> None:
    ch = charge({"slippage": {"bps": 5.0}}, [buy(1000), buy(4000)])
    assert ch.components["slippage"].tolist() == [5 * DOLLAR, 20 * DOLLAR]


def test_volatility_slippage_scales_with_volatility() -> None:
    section = {"vol_slippage": {"k": 0.5}}
    assert charge(section, [buy(100_000)], ctx(vol=0.02)).components["vol_slippage"].tolist() == [10_000 * DOLLAR]
    assert charge(section, [buy(100_000)], ctx(vol=0.0)).components["vol_slippage"].tolist() == [0]


def test_square_root_impact() -> None:
    # $1M into $100M ADV at 2% daily vol: 0.02 * sqrt(0.01) = 20 bp, $2,000.
    ch = charge({"impact": {"y": 1.0}}, [buy(100_000), buy(400_000)])
    assert ch.components["impact"].tolist() == [2_000 * DOLLAR, 16_000 * DOLLAR]
    # Four times the size: twice the cost per share.
    assert ch.participation.tolist() == pytest.approx([0.01, 0.04])


def test_latency_slippage_scales_with_the_square_root_of_latency() -> None:
    def cost(ms: float) -> int:
        return int(charge({"latency": {"latency_ms": ms, "k": 1.0}}, [buy(100_000)]).components["latency"][0])
    expected = math.ceil(0.02 * math.sqrt(0.1 / 23_400) * 1e12)
    assert cost(100) == expected
    assert cost(400) == pytest.approx(2 * expected, abs=1)
    assert cost(0) == 0


def test_too_little_history_charges_the_fallback_and_counts_it() -> None:
    c = ctx(n_obs=4, min_periods=5, fallback_bps=50)
    ch = charge({"impact": {"y": 1.0}, "spread": {"half_spread_bps": 2.0}}, [buy(100_000)], c)
    assert ch.components["impact"].tolist() == [5_000 * DOLLAR]  # 50 bp of $1M
    assert ch.components["spread"].tolist() == [200 * DOLLAR]
    assert ch.fallback.tolist() == [True]


def test_costs_are_integers_and_sum_to_the_total() -> None:
    section = {"commission": {"per_share": "0.0035", "bps": 0.1}, "spread": {"half_spread_bps": 2.0},
               "impact": {"y": 1.0}, "latency": {"latency_ms": 50}}
    ch = charge(section, [buy(1), buy(-7, price=123_457), buy(100_000)])
    for v in ch.components.values():
        assert v.dtype == np.int64 and (v >= 0).all()
    assert ch.total.tolist() == sum(ch.components.values()).tolist()
    # Even a one-micro-dollar notional pays a whole unit of spread.
    assert charge({"spread": {"half_spread_bps": 2.0}}, [buy(1, price=1)]).components["spread"].tolist() == [1]


# --- Configuration and modes ------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["spread", "slippage", "vol_slippage", "impact", "latency", "financing"])
def test_event_mode_refuses_what_the_fill_price_already_contains(name: str) -> None:
    params = {"spread": {"half_spread_bps": 1.0}, "slippage": {"bps": 1.0}, "vol_slippage": {"k": 1.0},
              "impact": {"y": 1.0}, "latency": {"latency_ms": 1}, "financing": {"borrow_bps": 1}}[name]
    with pytest.raises(ValueError, match="fill price"):
        build_cost_model({name: params}, "event")


@pytest.mark.parametrize("name", ["commission", "maker_taker", "sec_fee"])
def test_fees_are_allowed_in_both_modes(name: str) -> None:
    params = {"commission": {"per_share": "0.001"}, "maker_taker": {"taker_per_share": "0.001"},
              "sec_fee": {"schedule": "sec31"}}[name]
    for mode in ("bar", "event"):
        assert build_cost_model({name: params}, mode).names == (name,)


def test_a_component_can_be_switched_off() -> None:
    m = build_cost_model({"spread": {"half_spread_bps": 2.0, "enabled": False}, "impact": {"y": 1.0}}, "bar")
    assert m.names == ("impact",)


def test_unknown_components_and_keys_are_errors() -> None:
    with pytest.raises(ValueError, match="unknown cost component"):
        build_cost_model({"spred": {}}, "bar")
    with pytest.raises(ValueError, match="half_spread"):
        build_cost_model({"spread": {"half_spread": 2.0}}, "bar")


def test_the_model_describes_itself_for_the_record() -> None:
    m = build_cost_model({"commission": {"per_share": "0.0035"}, "sec_fee": {"schedule": "sec31"},
                          "impact": {"y": 1.0}, "financing": {"borrow_bps": 50, "spread_bps": 50}}, "bar")
    d = m.describe()
    assert d["commission"] == {"per_share": "0.0035", "bps": 0.0}
    assert d["sec_fee"]["schedule"] == load_schedule("sec31")  # the dated rows, not just a name
    assert d["impact"] == {"y": 1.0}
    assert d["financing"] == {"borrow_bps": 50.0, "spread_bps": 50.0, "day_count": "actual/360"}
    assert d["inputs"] == {"window": 20, "min_periods": 5, "fallback_bps": 50.0, "known_through": "bar before the fill"}


# --- Point-in-time inputs ------------------------------------------------------------------------------

def bars(close: list[float], volume: list[int] | None = None, split: list[int] | None = None) -> BarPanel:
    dates = pd.bdate_range("2020-01-01", periods=len(close))
    rows = [{"symbol": "A", "date": d, "open": int(c * DOLLAR), "high": int(c * DOLLAR), "low": int(c * DOLLAR),
             "close": int(c * DOLLAR), "volume": (volume or [1000] * len(close))[t], "dividend": 0,
             "split_num": (split or [1] * len(close))[t], "split_den": 1}
            for t, (d, c) in enumerate(zip(dates, close))]
    return BarPanel.from_frame(schema.normalize_bars_frame(pd.DataFrame(rows)))


def test_inputs_use_only_bars_before_the_fill() -> None:
    p = bars([10, 11, 10, 12, 12, 13], volume=[100, 200, 300, 400, 500, 600])
    inp = CostInputs.from_panel(p, window=3, min_periods=2)
    e = 4
    r = [11 / 10 - 1, 10 / 11 - 1, 12 / 10 - 1]  # returns of bars 1..3
    assert inp.vol[e, 0] == pytest.approx(float(np.std(r, ddof=1)))
    assert inp.adv[e, 0] == pytest.approx((200 * 11 + 300 * 10 + 400 * 12) * DOLLAR / 3)
    assert inp.n_obs[e, 0] == 3
    assert inp.n_obs[0, 0] == 0


def test_changing_the_fill_bar_or_later_leaves_its_inputs_unchanged() -> None:
    base = [10, 11, 10, 12, 12, 13, 12, 14]
    e = 5
    a = CostInputs.from_panel(bars(base, volume=[1000] * 8), window=3)
    later = CostInputs.from_panel(bars(base[:e] + [30, 1, 50], volume=[1000] * e + [9_999_999] * 3), window=3)
    assert a.vol[e, 0] == later.vol[e, 0] and a.adv[e, 0] == later.adv[e, 0]
    earlier = CostInputs.from_panel(bars(base[:e - 1] + [20] + base[e:], volume=[1000] * 8), window=3)
    assert a.vol[e, 0] != earlier.vol[e, 0]  # the bar before does count: the test can see a change


def test_dollar_adv_does_not_jump_on_a_split() -> None:
    # A 2:1 split on bar 2: share volume doubles, the price halves, dollar volume is unchanged.
    p = bars([10, 10, 5, 5, 5], volume=[1000, 1000, 2000, 2000, 2000], split=[1, 1, 2, 1, 1])
    inp = CostInputs.from_panel(p, window=3, min_periods=2)
    assert inp.adv[4, 0] == pytest.approx(10_000 * DOLLAR)
    assert inp.vol[4, 0] == pytest.approx(0.0)  # the split is not a return


# --- In the bar backtester -------------------------------------------------------------------------------

def flat(n: int = 5, price: int = 10) -> BarPanel:
    return bars([price] * n)


SECTION = {"commission": {"per_share": "0.0035"}, "spread": {"half_spread_bps": 2.0}}


def test_a_round_trip_at_a_constant_price_loses_exactly_its_costs() -> None:
    w = np.array([[1.0], [1.0], [0.0], [0.0], [0.0]])
    r = run_vectorized(flat(), w, 1_000 * DOLLAR, cost_model=build_cost_model(SECTION, "bar"))
    assert [(t.bar, t.qty) for t in r.trades] == [(1, 100), (3, -100)]
    per_trade = 200_000 + 100 * 3500  # 2 bp of $1,000 plus 100 x $0.0035
    assert r.gross_pnl == 0
    assert r.net_pnl == -2 * per_trade
    assert r.costs_by_component["spread"].tolist() == [0, 200_000, 0, 200_000, 0]
    assert (r.net_equity == r.equity - np.cumsum(sum(r.costs_by_component.values()))).all()


def test_positions_open_at_the_end_are_liquidated_at_the_last_close() -> None:
    p = bars([10, 11, 12, 13, 14])
    w = np.ones((5, 1))
    model = build_cost_model(SECTION, "bar")
    r = run_vectorized(p, w, 1_000 * DOLLAR, cost_model=model)
    kept = run_vectorized(p, w, 1_000 * DOLLAR, cost_model=model, liquidate_at_end=False)
    assert [t.kind for t in r.trades] == ["rebalance", "liquidation"]
    assert r.trades[-1].price == 14 * DOLLAR and r.trades[-1].bar == 4
    assert r.positions[-1, 0] == 0 and kept.positions[-1, 0] == 100
    assert (r.equity == kept.equity).all()  # marked at that close anyway: gross is unchanged
    assert r.costs > kept.costs


def test_trades_carry_the_decision_price_for_shortfall() -> None:
    p = bars([10, 11, 12, 13, 14])
    r = run_vectorized(p, np.ones((5, 1)), 1_000 * DOLLAR, BarExecution(lag=1, price="open"))
    t = r.trades[0]
    assert (t.signal_bar, t.bar, t.decision_price) == (0, 1, 10 * DOLLAR)


def test_the_decision_price_is_split_adjusted() -> None:
    p = bars([20, 10, 10, 10], split=[1, 2, 1, 1])
    r = run_vectorized(p, np.ones((4, 1)), 1_000 * DOLLAR, liquidate_at_end=False)
    assert r.trades[0].decision_price == 10 * DOLLAR  # 20 before a 2:1 split


def rates(day_rate_bp: int, start: str = "2019-12-01") -> RiskFree:
    return RiskFree.from_frame(pd.DataFrame({"date": [pd.Timestamp(start)], "rate": [day_rate_bp]}))


def test_cash_earns_the_risk_free_rate_in_gross() -> None:
    p = flat(4)
    r = run_vectorized(p, np.zeros((4, 1)), 1_000 * DOLLAR, risk_free=rates(500))
    daily = RiskFree.daily_rate(500)
    # 2020-01-01 is a Wednesday: bars Wed, Thu, Fri, Mon -> 1, 1, 3 calendar days.
    cash = 1_000 * DOLLAR
    expected = [cash]
    for days in (1, 1, 3):
        cash += math.floor(cash * days * daily)
        expected.append(cash)
    assert r.equity.tolist() == expected
    np.testing.assert_allclose(r.rf_returns, [0.0, float(daily), float(daily), float(3 * daily)])


def test_shorts_pay_borrow_and_debit_cash_pays_the_financing_spread() -> None:
    model = CostModel(financing=Financing(borrow_bps=3600, spread_bps=3600))
    p = flat(5)
    # Short $1,000 at $10: 100 shares. 36% a year on $1,000, actual/360: $1 a calendar day.
    short = run_vectorized(p, np.full((5, 1), -1.0), 1_000 * DOLLAR, cost_model=model, liquidate_at_end=False)
    assert short.costs_by_component["borrow"].tolist() == [0, 0, 1 * DOLLAR, 3 * DOLLAR, 1 * DOLLAR]
    assert short.costs_by_component["financing"].sum() == 0
    # Long 2x: $1,000 of cash borrowed, the same $1 a day.
    long2 = run_vectorized(p, np.full((5, 1), 2.0), 1_000 * DOLLAR, cost_model=model, liquidate_at_end=False)
    assert long2.costs_by_component["financing"].tolist() == [0, 0, 1 * DOLLAR, 3 * DOLLAR, 1 * DOLLAR]
    assert long2.costs_by_component["borrow"].sum() == 0


def test_costs_paid_also_lose_their_interest() -> None:
    p = flat(5)
    w = np.array([[1.0], [1.0], [0.0], [0.0], [0.0]])
    r = run_vectorized(p, w, 1_000 * DOLLAR, cost_model=build_cost_model(SECTION, "bar"), risk_free=rates(500))
    carry = r.costs_by_component["carry_on_costs"]
    assert carry[1] == 0 and carry[2] > 0  # from the bar after the first cost
    assert r.net_pnl == r.gross_pnl - r.costs


def test_walk_forward_selects_on_net_sharpe() -> None:
    # Odd bars return more. Weights decided at the close of t fill at t+1 (open = close in
    # this panel) and earn bar t+2, so leaning in on odd t is better gross, but it trades
    # every bar: with a 50 bp half-spread the steady weight wins net.
    rng = np.random.default_rng(4)
    t = np.arange(400)
    close = 50 * np.cumprod(1 + np.where(t % 2 == 1, 0.004, -0.001) + 0.005 * rng.standard_normal(400))
    p = bars(list(np.round(close, 2)))

    class Churn:
        def __init__(self, amplitude: float) -> None:
            self.amplitude = amplitude

        def weights(self, panel: BarPanel) -> np.ndarray:
            t = np.arange(len(panel.dates))
            return (1.0 + self.amplitude * (t % 2))[:, None]

    grid = [{"amplitude": 0.0}, {"amplitude": 0.6}]
    folds = make_folds(400, 200, 100)
    heavy = build_cost_model({"spread": {"half_spread_bps": 50.0}}, "bar")
    gross = walk_forward(p, Churn, grid, folds, 1_000_000 * DOLLAR)
    net = walk_forward(p, Churn, grid, folds, 1_000_000 * DOLLAR, cost_model=heavy)
    assert [f.params for f in gross.folds] == [{"amplitude": 0.6}] * 2
    assert [f.params for f in net.folds] == [{"amplitude": 0.0}] * 2
