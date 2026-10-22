"""The discrete-event loop around one engine (M8).

Everything that happens in a backtest is an action scheduled at a simulated time: a row
of historical data, an agent waking up, a strategy order arriving after its latency, a
timer. Actions run in ``(time, priority, scheduling order)`` order. An action may call
``Simulation.process``, which gives the command the next ``seq_no``, journals it, runs it
through ``Engine.process`` and delivers the events at once:

- events about an order (ack, reject, fill, cancel, replace) to the participant that
  owns it, and each ``Trade`` to the owners of both of its orders;
- a ``MarketData`` (top of book plus the trades) to every subscriber when the top of
  book changed or something traded.

Participants react by scheduling actions, never by processing commands from inside a
delivery, so one command's events are fully delivered before the next command runs.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, Protocol

import mfengine

ORDER_EVENTS = (mfengine.Ack, mfengine.Reject, mfengine.Fill, mfengine.Cancelled, mfengine.Replaced)


class Priority(IntEnum):
    """Tie-break at one timestamp: recorded history first (trades, then the quotes that
    show their result), then simulated agents, then the strategy, then its timers.
    Putting the strategy after the data it races is the conservative choice."""

    TRADE = 0
    QUOTE = 1
    AGENT = 2
    STRATEGY = 3
    TIMER = 4


@dataclass(frozen=True)
class PublicTrade:
    trade_id: int
    price: int
    qty: int
    aggressor: mfengine.Side


@dataclass(frozen=True)
class MarketData:
    """Top of book after a command, and what traded in it. An empty side is ``None``
    with size 0."""

    ts: int
    bid: int | None
    bid_qty: int
    ask: int | None
    ask_qty: int
    trades: tuple[PublicTrade, ...] = ()


class Participant(Protocol):
    def on_events(self, sim: Simulation, events: list[Any]) -> None: ...


class Subscriber(Protocol):
    def on_market_data(self, sim: Simulation, md: MarketData) -> None: ...


class ReentrantProcess(RuntimeError):
    """``process`` called while events were being delivered."""


class Simulation:
    def __init__(self, engine: mfengine.Engine | None = None, journal_path: str | Path | None = None) -> None:
        self.engine = engine if engine is not None else mfengine.Engine()
        self.now = 0
        self.commands = 0
        self._queue: list[tuple[int, int, int, Callable[[], None]]] = []
        self._counter = 0
        self._seq = self.engine.last_seq_no or 0
        self._next_order_id = 1
        self._owner: dict[int, Participant] = {}
        self._subscribers: list[Subscriber] = []
        self._delivering = False
        self._top: tuple[int | None, int, int | None, int] = (None, 0, None, 0)
        self._journal = mfengine.JournalWriter(journal_path) if journal_path is not None else None

    # --- setup ---------------------------------------------------------------------------

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def new_order_id(self, owner: Participant) -> int:
        oid = self._next_order_id
        self._next_order_id += 1
        self._owner[oid] = owner
        return oid

    def owner(self, order_id: int) -> Participant | None:
        return self._owner.get(order_id)

    # --- scheduling -------------------------------------------------------------------------

    def at(self, ts: int, priority: Priority, action: Callable[[], None]) -> None:
        if ts < self.now:
            raise ValueError(f"cannot schedule at {ts}: the simulation is at {self.now}")
        heapq.heappush(self._queue, (ts, int(priority), self._counter, action))
        self._counter += 1

    def run(self) -> None:
        while self._queue:
            ts, _, _, action = heapq.heappop(self._queue)
            self.now = ts
            action()

    def close(self) -> None:
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    # --- commands ------------------------------------------------------------------------------

    def process(self, command: Any) -> list[Any]:
        if self._delivering:
            raise ReentrantProcess("schedule an action instead of processing during a delivery")
        self._seq += 1
        command = command.with_seq_no(self._seq)
        if self._journal is not None:
            self._journal.append(mfengine.JournalRecord(timestamp=self.now, command=command))
        events = self.engine.process(command, self.now)
        self.commands += 1
        self._deliver(events)
        return events

    def _deliver(self, events: list[Any]) -> None:
        by_owner: dict[int, tuple[Participant, list[Any]]] = {}
        trades = []

        def give(order_id: int, event: Any) -> None:
            owner = self._owner.get(order_id)
            if owner is not None:
                by_owner.setdefault(id(owner), (owner, []))[1].append(event)

        for e in events:
            if isinstance(e, mfengine.Trade):
                trades.append(PublicTrade(e.trade_id, e.price, e.qty, e.aggressor_side))
                give(e.maker_order_id, e)
                if self._owner.get(e.taker_order_id) is not self._owner.get(e.maker_order_id):
                    give(e.taker_order_id, e)
            elif isinstance(e, ORDER_EVENTS):
                give(e.order_id, e)

        top = self._read_top()
        md = None
        if trades or top != self._top:
            self._top = top
            md = MarketData(self.now, *top, trades=tuple(trades))
        self._delivering = True
        try:
            for owner, owned in by_owner.values():
                owner.on_events(self, owned)
            if md is not None:
                for s in self._subscribers:
                    s.on_market_data(self, md)
        finally:
            self._delivering = False

    def _read_top(self) -> tuple[int | None, int, int | None, int]:
        bid, ask = self.engine.best_bid(), self.engine.best_ask()
        bq = self.engine.depth(mfengine.Side.BUY, 1)[0][1] if bid is not None else 0
        aq = self.engine.depth(mfengine.Side.SELL, 1)[0][1] if ask is not None else 0
        return bid, bq, ask, aq

    @property
    def top(self) -> tuple[int | None, int, int | None, int]:
        return self._top
