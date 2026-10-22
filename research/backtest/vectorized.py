"""The bar backtester for fast parameter sweeps (M8; costs and financing M9).

Input: a ``BarPanel`` and target weights ``w[t, j]`` decided at the close of bar ``t``
(from bars ``<= t`` only; ``research.backtest.causality`` checks that). Execution rules:

- A weight trades at bar ``t + lag``, ``lag >= 1`` (default 1), at that bar's open
  (default) or close. Trading on the bar that produced the signal is impossible to
  configure: that is the look-ahead the lag exists to prevent.
- Target shares are ``trunc(w * capital / close[t])``, sized off the fixed starting
  capital (no compounding) with the signal bar's close, truncated toward zero: whole
  shares, never more than the weight asks for.
- Shares are re-sized only when a symbol's weight changes, so a constant weight is held
  as a constant share count (no daily rebalancing to a fixed weight).
- A symbol without a bar on the execution day trades on its next bar.
- Splits scale positions (and pending targets) on the ex-date, fractional shares paid in
  cash at the close; dividends are paid on shares held into the ex-date. A target sized
  before a split and filled on or after its ex-date is scaled too (M9 fix: M8 filled it
  in pre-split shares when the fill bar was the ex-date).
- Positions still open on the window's last bar are liquidated at its close
  (``liquidate_at_end``, default on): each window starts flat, so without this the
  entries would be charged and the exits not. Gross equity is unchanged by it (the
  position was marked at that close anyway).

**Gross and net.** Gross is the frictionless result: fills at the print, dividends,
splits, and cash (short-sale proceeds included) earning the risk-free rate, debits
paying it (``research.backtest.rates``). Net subtracts, per bar: each trade's costs
(``research.backtest.costs``); ``borrow`` on short value at the previous close and
``financing`` (the spread over the risk-free rate) on a debit balance, both actual/360
on calendar days; and ``carry_on_costs``, the risk-free interest the costs paid so far
would have earned. So ``net = gross - sum of components`` exactly.

Everything that is money or shares is int64 (or a Python int); floats only size
positions and summarize. The loop runs once per bar with NumPy across symbols.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from research.backtest.bars import BarPanel
from research.backtest.costs import CostModel, TradeRecord, ZeroCost
from research.backtest.rates import RiskFree, accrue
from research.data.schema import BAR_PRICE_UNIT
from research.data.schema import DIVIDEND_UNITS_PER_PRICE_UNIT as DIV_SCALE

FINANCING_COMPONENTS = ("borrow", "financing", "carry_on_costs")


@dataclass(frozen=True)
class BarExecution:
    lag: int = 1
    price: str = "open"

    def __post_init__(self) -> None:
        if not isinstance(self.lag, int) or self.lag < 1:
            raise ValueError(f"lag must be an integer >= 1 (a signal at the close of t trades at t+lag), got {self.lag}")
        if self.price not in ("open", "close"):
            raise ValueError(f"fill price must be 'open' or 'close', not {self.price!r}")


@dataclass
class BarResult:
    dates: np.ndarray
    equity: np.ndarray       # int64, gross, marked at the close
    pnl: np.ndarray          # int64, gross, per bar
    net_equity: np.ndarray   # int64, after costs
    net_pnl_per_bar: np.ndarray
    positions: np.ndarray    # int64 [bars, N] after the bar's trades
    trades: list[TradeRecord]
    costs: int
    gross_pnl: int
    net_pnl: int
    capital: int
    # M9
    costs_by_component: dict[str, np.ndarray]   # int64 per bar
    trade_costs: dict[str, np.ndarray]          # int64 per trade
    trade_fallback: np.ndarray                  # bool per trade
    trade_participation: np.ndarray             # float per trade (nan: unknown)
    rf_returns: np.ndarray                      # risk-free return of each bar on cash
    held: np.ndarray                            # int64 [bars, N] held into the bar (after splits)
    old_leg: np.ndarray                         # int64 [bars, N] P&L of the position held into the bar
    new_leg: np.ndarray                         # int64 [bars, N] P&L of the position after its trades
    symbol_costs: np.ndarray                    # int64 [bars, N] trade costs
    gross_exposure: np.ndarray                  # int64 per bar
    net_exposure: np.ndarray                    # int64 per bar
    split_ratio: np.ndarray                     # float [bars, N]
    start: int = 0

    @property
    def returns(self) -> np.ndarray:
        """Net per-bar returns on the starting capital."""
        return self.net_pnl_per_bar / self.capital

    @property
    def gross_returns(self) -> np.ndarray:
        return self.pnl / self.capital


def _split(qty: np.ndarray, num: np.ndarray, den: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shares after a num:den split, truncated toward zero, and the remainder in
    1/den new-share units (signed)."""
    scaled = np.abs(qty) * num
    whole = scaled // den
    return np.sign(qty) * whole, np.sign(qty) * (scaled - whole * den)


def run_vectorized(panel: BarPanel, weights: np.ndarray, capital: int, execution: BarExecution = BarExecution(),
                   cost_model: CostModel | None = None, start: int = 0, end: int | None = None, *,
                   risk_free: RiskFree | None = None, liquidate_at_end: bool = True,
                   money_unit: str = BAR_PRICE_UNIT) -> BarResult:
    T, N = panel.shape
    end = T if end is None else end
    cost_model = ZeroCost() if cost_model is None else cost_model
    risk_free = RiskFree.zero() if risk_free is None else risk_free
    weights = np.asarray(weights, dtype=float)
    if weights.shape != (T, N):
        raise ValueError(f"weights have shape {weights.shape}, the panel {(T, N)}")
    if not np.isfinite(weights).all():
        raise ValueError("weights must be finite (use 0 for no position)")
    if not 0 <= start < end <= T:
        raise ValueError(f"bad window [{start}, {end}) for {T} bars")

    ctx = cost_model.bar_context(panel, money_unit) if cost_model.components else None
    fin = cost_model.financing
    accrual = risk_free.per_bar(panel.dates[start:end])
    dates = panel.dates[start:end].astype("datetime64[D]")
    lag = execution.lag
    fill = panel.open if execution.price == "open" else panel.close
    pos = np.zeros(N, np.int64)
    applied = np.zeros(N)                    # the weight each position was last sized from
    pending = np.zeros(N, np.int64)          # target shares waiting for a bar to trade on
    has_pending = np.zeros(N, bool)
    pending_signal = np.zeros(N, np.int64)
    pending_decision = [Fraction(0)] * N     # signal close, split-adjusted to the pending shares
    last_close = np.zeros(N, np.int64)
    cash = int(capital)
    paid = 0                                 # costs charged so far (they reduce the net cash)
    n = end - start
    equity = np.zeros(n, np.int64)
    positions = np.zeros((n, N), np.int64)
    held_arr = np.zeros((n, N), np.int64)
    old_leg = np.zeros((n, N), np.int64)
    new_leg = np.zeros((n, N), np.int64)
    sym_costs = np.zeros((n, N), np.int64)
    split_ratio = np.ones((n, N))
    gross_exp = np.zeros(n, np.int64)
    net_exp = np.zeros(n, np.int64)
    by_bar = {name: np.zeros(n, np.int64) for name in (*cost_model.names, *FINANCING_COMPONENTS)}
    trades: list[TradeRecord] = []
    trade_costs: dict[str, list[np.ndarray]] = {name: [] for name in cost_model.names}
    fallback: list[np.ndarray] = []
    participation: list[np.ndarray] = []

    for e in range(start, end):
        i = e - start
        valid = panel.valid[e]
        # Balances held since the previous bar: risk-free interest (gross), financing (costs).
        if i > 0:
            rate = accrual[i]
            if rate:
                cash += accrue(cash, rate)
                by_bar["carry_on_costs"][i] = accrue(paid, rate, cost=True)
            if fin is not None:
                days = int((dates[i] - dates[i - 1]).astype(np.int64))
                short_value = int(np.sum(np.where(pos < 0, -pos * last_close, 0)))
                if fin.borrow_bps and short_value:
                    by_bar["borrow"][i] = accrue(short_value, days * fin.daily(fin.borrow_bps), cost=True)
                if fin.spread_bps and cash < 0:
                    by_bar["financing"][i] = accrue(-cash, days * fin.daily(fin.spread_bps), cost=True)
        prev_pos, prev_close = pos.copy(), last_close.copy()
        flows = np.zeros(N, np.int64)        # split cash in lieu and dividends, per symbol
        # Corporate actions on shares held into today.
        splits = valid & (panel.split_num[e] != panel.split_den[e])
        if splits.any():
            num, den = panel.split_num[e], panel.split_den[e]
            new_pos, rem = _split(pos, num, den)
            flows += np.where(splits, rem * panel.close[e] // den, 0)
            pos = np.where(splits, new_pos, pos)
            for j in np.flatnonzero(splits & has_pending):
                pending_decision[j] = pending_decision[j] * int(den[j]) / int(num[j])
            pending = np.where(splits & has_pending, _split(pending, num, den)[0], pending)
            split_ratio[i] = np.where(splits, num / den, 1.0)
        # Dividends are in 1/DIVIDEND_UNITS_PER_PRICE_UNIT price units; floor division pays a
        # long the floor and charges a short the ceiling.
        flows += np.where(valid, pos * panel.dividend[e] // DIV_SCALE, 0)
        cash += int(flows.sum())
        held = pos.copy()
        # Signals from the close of e - lag.
        s = e - lag
        if s >= 0:
            w = weights[s]
            changed = (w != applied) & panel.valid[s]
            if changed.any():
                target = np.trunc(w * capital / np.where(panel.close[s] > 0, panel.close[s], 1)).astype(np.int64)
                decision = {int(j): Fraction(int(panel.close[s, j])) for j in np.flatnonzero(changed)}
                # Sized in the signal bar's shares: a split since then (on bars s+1..e, this
                # one included) applies to the target as it did to the position.
                for k in range(s + 1, e + 1):
                    num, den = panel.split_num[k], panel.split_den[k]
                    split_k = changed & panel.valid[k] & (num != den)
                    if split_k.any():
                        target = np.where(split_k, _split(target, num, den)[0], target)
                        for j in np.flatnonzero(split_k):
                            decision[int(j)] = decision[int(j)] * int(den[j]) / int(num[j])
                pending = np.where(changed, target, pending)
                has_pending |= changed
                applied = np.where(changed, w, applied)
                pending_signal = np.where(changed, s, pending_signal)
                for j, d in decision.items():
                    pending_decision[j] = d
        # Execute what can trade today.
        ready = has_pending & valid
        today: list[TradeRecord] = []
        # Where each symbol's bar splits into its two P&L legs: the fill if it traded, else
        # the close (or, with no bar today, the last close: both legs are then zero).
        traded_at = np.where(valid, panel.close[e], last_close).astype(np.int64)
        for j in np.flatnonzero(ready):
            qty = int(pending[j] - pos[j])
            if qty:
                price = int(fill[e, j])
                traded_at[j] = price
                today.append(TradeRecord(symbol=int(j), qty=qty, price=price, bar=e, volume=int(panel.volume[e, j]),
                                         signal_bar=int(pending_signal[j]),
                                         decision_price=round(pending_decision[j])))
        pos = np.where(ready, pending, pos)
        has_pending &= ~ready
        last_close = np.where(valid, panel.close[e], last_close)
        # The position held into the bar earns yesterday's close to the split price; the
        # position after the bar's trades earns the split price to the close. Summed over
        # symbols, plus interest, they are the bar's gross P&L.
        old_leg[i] = held * traded_at - prev_pos * prev_close + flows
        new_leg[i] = pos * (last_close - traded_at)
        if liquidate_at_end and e == end - 1:
            # The window ends flat, at the close: no P&L against the mark, only costs.
            for j in np.flatnonzero(pos):
                close = int(last_close[j])
                today.append(TradeRecord(symbol=int(j), qty=-int(pos[j]), price=close, bar=e,
                                         volume=int(panel.volume[e, j]), signal_bar=e, decision_price=close,
                                         kind="liquidation"))
            pos = np.zeros(N, np.int64)
            has_pending[:] = False
        for t in today:
            cash -= t.qty * t.price
        # Costs of today's trades.
        if today and ctx is not None:
            ch = cost_model.charge(today, ctx)
            for name, v in ch.components.items():
                by_bar[name][i] += int(v.sum())
                trade_costs[name].append(v)
            for t, c in zip(today, ch.total):
                sym_costs[i, t.symbol] += int(c)
            fallback.append(ch.fallback)
            participation.append(ch.participation)
        elif today:
            fallback.append(np.zeros(len(today), bool))
            participation.append(np.full(len(today), np.nan))
        trades.extend(today)
        paid += sum(int(v[i]) for v in by_bar.values())
        value = pos * last_close
        equity[i] = cash + int(np.sum(value))
        positions[i] = pos
        held_arr[i] = held
        gross_exp[i] = int(np.sum(np.abs(value)))
        net_exp[i] = int(np.sum(value))

    costs_per_bar = sum(by_bar.values(), np.zeros(n, np.int64))
    net_equity = equity - np.cumsum(costs_per_bar)
    pnl = np.diff(equity, prepend=capital)
    net_pnl_per_bar = np.diff(net_equity, prepend=capital)
    trade_cost_arrays = {name: (np.concatenate(v) if v else np.zeros(0, np.int64)) for name, v in trade_costs.items()}
    return BarResult(
        dates=panel.dates[start:end], equity=equity, pnl=pnl, net_equity=net_equity,
        net_pnl_per_bar=net_pnl_per_bar, positions=positions, trades=trades, costs=int(costs_per_bar.sum()),
        gross_pnl=int(equity[-1] - capital), net_pnl=int(net_equity[-1] - capital), capital=capital,
        costs_by_component=by_bar, trade_costs=trade_cost_arrays,
        trade_fallback=np.concatenate(fallback) if fallback else np.zeros(0, bool),
        trade_participation=np.concatenate(participation) if participation else np.zeros(0),
        rf_returns=np.array([float(a) for a in accrual]), held=held_arr, old_leg=old_leg, new_leg=new_leg,
        symbol_costs=sym_costs, gross_exposure=gross_exp, net_exposure=net_exp, split_ratio=split_ratio, start=start)
