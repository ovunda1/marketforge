"""The broker adapter between a strategy and the engine (M8).

It turns a strategy's intents into engine commands under the strategy's account, and
the engine's events back into fills and order updates. Each intent reaches the engine
``latency_ns`` (plus seeded jitter in ``[0, jitter_ns]``) after the strategy decided,
and never before an earlier intent: one session to the exchange, so orders cannot
overtake each other. The ``seq_no`` is given by the simulation when the command
actually reaches the engine, in arrival order with all other flow.
"""

from __future__ import annotations

from typing import Any

import mfengine
import numpy as np

from research.backtest.loop import MarketData, Priority, Simulation
from research.backtest.strategy import Context, MarketView, OrderUpdate, Strategy, StrategyFill, _Clock


class Broker:
    def __init__(self, sim: Simulation, strategy: Strategy, *, account: int, latency_ns: int, jitter_ns: int,
                 seed: int, session_start: int, session_end: int) -> None:
        self.sim = sim
        self.strategy = strategy
        self.account = account
        self.latency_ns = latency_ns
        self.jitter_ns = jitter_ns
        jitter_seed, strategy_seed = np.random.SeedSequence(seed).spawn(2)
        self._jitter = np.random.default_rng(jitter_seed)
        self.session_start = session_start
        self.session_end = session_end
        self.position = 0
        self.cash = 0
        self.fills: list[StrategyFill] = []
        self.updates: list[OrderUpdate] = []
        self._clock = _Clock()
        self.view = MarketView(self._clock)
        self.ctx = Context(self, self._clock, self.view, session_start, session_end,
                           np.random.default_rng(strategy_seed))
        self._next_client_id = 1
        self._order_of: dict[int, int] = {}   # client id -> engine order id
        self._client_of: dict[int, int] = {}  # engine order id -> client id
        self._open: dict[int, int] = {}       # client id -> leaves
        self._last_arrival = 0
        self._ended = False
        sim.subscribe(self)

    # --- intents ------------------------------------------------------------------------------

    def _send(self, command: Any) -> None:
        if self._ended:
            raise RuntimeError("the session has ended: on_end cannot send orders")
        delay = self.latency_ns
        if self.jitter_ns:
            delay += int(self._jitter.integers(0, self.jitter_ns + 1))
        arrival = max(self.sim.now + delay, self._last_arrival)
        self._last_arrival = arrival
        self.sim.at(arrival, Priority.STRATEGY, lambda: self.sim.process(command))

    def submit(self, side: mfengine.Side, qty: int, price: int | None, tif: mfengine.TimeInForce,
               post_only: bool) -> int:
        cid = self._next_client_id
        self._next_client_id += 1
        oid = self.sim.new_order_id(self)
        self._order_of[cid], self._client_of[oid] = oid, cid
        if price is None:
            command = mfengine.NewOrder(seq_no=0, order_id=oid, account_id=self.account, side=side, qty=qty,
                                        type=mfengine.OrderType.MARKET, tif=mfengine.TimeInForce.IOC)
        else:
            command = mfengine.NewOrder(seq_no=0, order_id=oid, account_id=self.account, side=side, qty=qty,
                                        price=price, tif=tif, post_only=post_only)
        self._send(command)
        return cid

    def _engine_id(self, client_id: int) -> int:
        if client_id not in self._order_of:
            raise ValueError(f"unknown client order id {client_id}")
        return self._order_of[client_id]

    def cancel(self, client_id: int) -> None:
        self._send(mfengine.Cancel(seq_no=0, order_id=self._engine_id(client_id)))

    def replace(self, client_id: int, price: int, qty: int) -> None:
        """``qty`` is the new total quantity, filled part included (engine semantics)."""
        self._send(mfengine.Replace(seq_no=0, order_id=self._engine_id(client_id), price=price, qty=qty))

    def open_orders(self) -> dict[int, int]:
        return dict(self._open)

    def set_timer(self, interval_ns: int) -> None:
        if interval_ns <= 0:
            raise ValueError("timer interval must be positive")

        def fire(t: int) -> None:
            self._clock.now = t
            self.strategy.on_timer(self.ctx, t)
            if t + interval_ns <= self.session_end:
                self.sim.at(t + interval_ns, Priority.TIMER, lambda: fire(t + interval_ns))

        first = self.session_start + interval_ns
        if first <= self.session_end:
            self.sim.at(first, Priority.TIMER, lambda: fire(first))

    # --- lifecycle -------------------------------------------------------------------------------

    def start(self) -> None:
        self._clock.now = self.session_start
        self.strategy.on_start(self.ctx)

    def end(self) -> None:
        self._clock.now = max(self.sim.now, self.session_end)
        self._ended = True
        self.strategy.on_end(self.ctx)

    # --- engine -> strategy --------------------------------------------------------------------------

    def on_market_data(self, sim: Simulation, md: MarketData) -> None:
        self._clock.now = md.ts
        self.view._record(md)
        self.strategy.on_market_data(self.ctx, md)

    def on_events(self, sim: Simulation, events: list[Any]) -> None:
        self._clock.now = sim.now
        for e in events:
            if isinstance(e, mfengine.Trade):
                continue
            cid = self._client_of[e.order_id]
            if isinstance(e, mfengine.Fill):
                signed = e.qty if e.side == mfengine.Side.BUY else -e.qty
                self.position += signed
                self.cash -= signed * e.price
                if e.leaves_qty:
                    self._open[cid] = e.leaves_qty
                else:
                    self._open.pop(cid, None)
                fill = StrategyFill(sim.now, cid, e.side, e.price, e.qty, e.liquidity, e.leaves_qty)
                self.fills.append(fill)
                self.strategy.on_fill(self.ctx, fill)
                continue
            if isinstance(e, mfengine.Ack):
                self._open[cid] = e.qty
                update = OrderUpdate(sim.now, cid, "ack")
            elif isinstance(e, mfengine.Reject):
                update = OrderUpdate(sim.now, cid, "reject", e.reason.name)
            elif isinstance(e, mfengine.Cancelled):
                self._open.pop(cid, None)
                update = OrderUpdate(sim.now, cid, "cancelled", e.reason.name, e.cancelled_qty)
            else:  # Replaced
                self._open[cid] = e.leaves_qty
                update = OrderUpdate(sim.now, cid, "replaced")
            self.updates.append(update)
            self.strategy.on_order_update(self.ctx, update)
