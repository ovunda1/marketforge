"""Agents of the synthetic market (M8). Each trades through the real engine under its own
account(s) and reacts to what it is told; all randomness comes from the generator it is
given (``research.sim.market`` seeds them all from one seed).

- ``FairValue``: a hidden integer random walk, the "true" price noise traders and the
  market maker anchor to. Nobody trades it directly.
- ``NoiseTraders``: Poisson arrivals; each one cancels a resting order, sends a market
  order, or rests a limit order a few ticks around fair value.
- ``MarketMaker``: two-sided post-only quotes around fair value, shifted against its
  inventory, re-quoted on a timer and after every fill.
- ``MomentumTrader``: fast and slow time-decayed averages of trade prices; buys (sells)
  at market when the fast one is above (below) the slow one by a threshold.

Floats appear only in agents' decisions (rates, averages), never in prices or sizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import mfengine
import numpy as np

from research.backtest.loop import MarketData, Priority, Simulation

SEC = 1_000_000_000
BUY, SELL = mfengine.Side.BUY, mfengine.Side.SELL


@dataclass(frozen=True)
class FairValueConfig:
    step_ns: int = 100_000_000
    max_step_ticks: int = 1


@dataclass(frozen=True)
class NoiseConfig:
    rate_per_sec: float = 50.0
    p_market: float = 0.1
    p_cancel: float = 0.3
    max_offset_ticks: int = 8
    max_qty: int = 10
    accounts: int = 16


@dataclass(frozen=True)
class MarketMakerConfig:
    half_spread_ticks: int = 2
    qty: int = 20
    inventory_per_tick: int = 20
    max_inventory: int = 200
    requote_ns: int = 200_000_000


@dataclass(frozen=True)
class MomentumConfig:
    count: int = 2
    fast_halflife_ns: int = 1 * SEC
    slow_halflife_ns: int = 10 * SEC
    threshold_ticks: float = 1.0
    qty: int = 5
    cooldown_ns: int = 2 * SEC
    max_position: int = 50


def _at(sim: Simulation, end: int | None, ts: int, action: Any) -> None:
    """Schedule an agent's own wake-up, unless it falls after the market closes."""
    if end is None or ts <= end:
        sim.at(ts, Priority.AGENT, action)


class FairValue:
    def __init__(self, sim: Simulation, config: FairValueConfig, start: int, rng: np.random.Generator,
                 end: int | None = None) -> None:
        self.sim, self.config, self.value, self.rng, self.end = sim, config, start, rng, end

    def schedule(self) -> None:
        _at(self.sim, self.end, self.sim.now + self.config.step_ns, self._step)

    def _step(self) -> None:
        k = self.config.max_step_ticks
        self.value = max(10, self.value + int(self.rng.integers(-k, k + 1)))
        self.schedule()


class _Agent:
    """Order bookkeeping shared by the agents: what is resting, the position."""

    end: int | None = None  # no wake-ups after this time

    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self.resting: dict[int, int] = {}  # order id -> leaves
        self.position = 0
        self.fills = 0

    def on_events(self, sim: Simulation, events: list[Any]) -> None:
        for e in events:
            if isinstance(e, mfengine.Fill):
                self.position += e.qty if e.side == BUY else -e.qty
                self.fills += 1
                if e.leaves_qty:
                    self.resting[e.order_id] = e.leaves_qty
                else:
                    self.resting.pop(e.order_id, None)
                self.on_fill(e)
            elif isinstance(e, mfengine.Ack) and e.tif == mfengine.TimeInForce.GTC:
                self.resting[e.order_id] = e.qty
            elif isinstance(e, (mfengine.Cancelled, mfengine.Reject)):
                self.resting.pop(e.order_id, None)

    def on_fill(self, fill: mfengine.Fill) -> None: ...

    def _new(self, account: int, side: mfengine.Side, qty: int, price: int | None = None,
             post_only: bool = False) -> int:
        oid = self.sim.new_order_id(self)
        if price is None:
            cmd = mfengine.NewOrder(seq_no=0, order_id=oid, account_id=account, side=side, qty=qty,
                                    type=mfengine.OrderType.MARKET, tif=mfengine.TimeInForce.IOC)
        else:
            cmd = mfengine.NewOrder(seq_no=0, order_id=oid, account_id=account, side=side, qty=qty, price=price,
                                    post_only=post_only)
        self.sim.process(cmd)
        return oid


class NoiseTraders(_Agent):
    BASE_ACCOUNT = 1000

    def __init__(self, sim: Simulation, config: NoiseConfig, fair: FairValue, rng: np.random.Generator) -> None:
        super().__init__(sim)
        self.config, self.fair, self.rng = config, fair, rng
        self.arrivals = 0
        self._mean_gap_ns = SEC / config.rate_per_sec

    def schedule(self) -> None:
        gap = max(1, int(self.rng.exponential(self._mean_gap_ns)))
        _at(self.sim, self.end, self.sim.now + gap, self._arrive)

    def _arrive(self) -> None:
        self.arrivals += 1
        c, rng = self.config, self.rng
        u = rng.random()
        if u < c.p_cancel and self.resting:
            oids = sorted(self.resting)
            self.sim.process(mfengine.Cancel(seq_no=0, order_id=oids[int(rng.integers(len(oids)))]))
        else:
            account = self.BASE_ACCOUNT + int(rng.integers(c.accounts))
            side = BUY if rng.random() < 0.5 else SELL
            qty = int(rng.integers(1, c.max_qty + 1))
            if u < c.p_cancel + c.p_market:
                self._new(account, side, qty)
            else:
                # -1 is one tick through fair value: some limit orders cross.
                offset = int(rng.integers(-1, c.max_offset_ticks + 1))
                price = self.fair.value - offset if side == BUY else self.fair.value + offset
                self._new(account, side, qty, max(1, price))
        self.schedule()


class MarketMaker(_Agent):
    def __init__(self, config: MarketMakerConfig, account_id: int, sim: Simulation | None = None,
                 fair: FairValue | None = None) -> None:
        super().__init__(sim)  # type: ignore[arg-type]
        self.config, self.account, self.fair = config, account_id, fair
        self.quotes: dict[mfengine.Side, tuple[int, int] | None] = {BUY: None, SELL: None}  # (oid, price)
        self._requote_pending = False

    def quote_prices(self, fair: int, inventory: int) -> tuple[int | None, int | None]:
        """Bid and ask around ``fair``, both shifted one tick against every
        ``inventory_per_tick`` units held; a side that would grow the inventory past
        ``max_inventory`` is not quoted."""
        c = self.config
        steps = abs(inventory) // c.inventory_per_tick
        shift = -steps if inventory > 0 else steps
        bid = fair - c.half_spread_ticks + shift
        ask = fair + c.half_spread_ticks + shift
        return (None if inventory >= c.max_inventory else max(1, bid),
                None if inventory <= -c.max_inventory else ask)

    def schedule(self) -> None:
        self._timer()

    def _timer(self) -> None:
        self._refresh()
        _at(self.sim, self.end, self.sim.now + self.config.requote_ns, self._timer)

    def _refresh(self) -> None:
        """Keep a quote that is resting in full at the right price; replace the rest."""
        self._requote_pending = False
        bid, ask = self.quote_prices(self.fair.value, self.position)
        for side, price in ((BUY, bid), (SELL, ask)):
            current = self.quotes[side]
            if current is not None:
                oid, at = current
                if at == price and self.resting.get(oid) == self.config.qty:
                    continue
                if oid in self.resting:
                    self.sim.process(mfengine.Cancel(seq_no=0, order_id=oid))
                self.quotes[side] = None
            if price is not None:
                oid = self._new(self.account, side, self.config.qty, price, post_only=True)
                self.quotes[side] = (oid, price) if oid in self.resting else None

    def on_fill(self, fill: mfengine.Fill) -> None:
        # Re-quote right after a fill, once (several fills can arrive in one command).
        if not self._requote_pending:
            self._requote_pending = True
            self.sim.at(self.sim.now, Priority.AGENT, self._refresh)


class _Ewma:
    def __init__(self, halflife_ns: int) -> None:
        self.halflife_ns = halflife_ns
        self.value: float | None = None
        self.t = 0

    def update(self, t: int, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            w = 0.5 ** ((t - self.t) / self.halflife_ns)
            self.value = w * self.value + (1 - w) * x
        self.t = t
        return self.value


class MomentumTrader(_Agent):
    def __init__(self, config: MomentumConfig, account_id: int, sim: Simulation | None = None) -> None:
        super().__init__(sim)  # type: ignore[arg-type]
        self.config, self.account = config, account_id
        self._fast, self._slow = _Ewma(config.fast_halflife_ns), _Ewma(config.slow_halflife_ns)
        self._last_decision: int | None = None

    def on_trade_price(self, ts: int, price: int) -> mfengine.Side | None:
        """Update the averages with a trade; the side to trade now, if any."""
        score = self._fast.update(ts, price) - self._slow.update(ts, price)
        c = self.config
        if self._last_decision is not None and ts < self._last_decision + c.cooldown_ns:
            return None
        if score > c.threshold_ticks and self.position + c.qty <= c.max_position:
            side = BUY
        elif score < -c.threshold_ticks and self.position - c.qty >= -c.max_position:
            side = SELL
        else:
            return None
        self._last_decision = ts
        return side

    def on_market_data(self, sim: Simulation, md: MarketData) -> None:
        for t in md.trades:
            side = self.on_trade_price(md.ts, t.price)
            if side is not None:
                # React after this command's delivery, at the same time.
                sim.at(sim.now, Priority.AGENT, lambda s=side: self._new(self.account, s, self.config.qty))
