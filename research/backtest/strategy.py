"""The strategy interface (M8).

A strategy never holds a dataset or the engine. It is called back with what has
happened (``on_market_data``, ``on_fill``, ``on_order_update``, ``on_timer``) and reads
history only through ``ctx.view``, which holds exactly the market data delivered so
far. Asking it about a time after ``ctx.now`` raises ``LookAheadError``. That is how
look-ahead is prevented: the future is not reachable from the strategy, rather than
being off limits by convention.

Orders are intents: ``ctx.buy``/``ctx.sell``/``ctx.cancel``/``ctx.replace`` hand them to
the broker (``research.backtest.broker``), which sends them to the engine after the
configured latency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import mfengine
import numpy as np

from research.backtest.loop import MarketData

if TYPE_CHECKING:
    from research.backtest.broker import Broker

__all__ = ["Context", "LookAheadError", "MarketData", "MarketView", "OrderUpdate", "Strategy", "StrategyFill"]


class LookAheadError(RuntimeError):
    """Something asked for data from after the current simulated time."""


@dataclass(frozen=True)
class StrategyFill:
    ts: int
    client_id: int
    side: mfengine.Side
    price: int
    qty: int
    liquidity: mfengine.Liquidity
    leaves_qty: int


@dataclass(frozen=True)
class OrderUpdate:
    """``kind`` is ``ack``, ``reject``, ``cancelled`` or ``replaced``; ``reason`` is the
    engine's reject or cancel reason name; ``qty`` the quantity cancelled, if any."""

    ts: int
    client_id: int
    kind: str
    reason: str | None = None
    qty: int = 0


class _Column:
    """A growable int64 buffer that hands out read-only views of its filled part."""

    def __init__(self) -> None:
        self._buf = np.zeros(1024, dtype=np.int64)
        self.n = 0

    def append(self, value: int) -> None:
        if self.n == len(self._buf):
            self._buf = np.concatenate([self._buf, np.zeros_like(self._buf)])
        self._buf[self.n] = value
        self.n += 1

    def view(self) -> np.ndarray:
        v = self._buf[: self.n]
        v.flags.writeable = False
        return v


class MarketView:
    """Market data delivered so far. Quote columns use 0 for an empty side."""

    QUOTE_FIELDS = ("ts", "bid", "bid_qty", "ask", "ask_qty")
    TRADE_FIELDS = ("ts", "price", "qty", "aggressor")

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self._q = {k: _Column() for k in self.QUOTE_FIELDS}
        self._t = {k: _Column() for k in self.TRADE_FIELDS}

    def _record(self, md: MarketData) -> None:
        for k, v in zip(self.QUOTE_FIELDS, (md.ts, md.bid or 0, md.bid_qty, md.ask or 0, md.ask_qty)):
            self._q[k].append(v)
        for t in md.trades:
            for k, v in zip(self.TRADE_FIELDS, (md.ts, t.price, t.qty, t.aggressor.value)):
                self._t[k].append(v)

    def quotes(self) -> dict[str, np.ndarray]:
        return {k: c.view() for k, c in self._q.items()}

    def trades(self) -> dict[str, np.ndarray]:
        return {k: c.view() for k, c in self._t.items()}

    def asof(self, ts: int) -> MarketData | None:
        """The top of book as it was at ``ts`` (the last update at or before it)."""
        if ts > self._clock.now:
            raise LookAheadError(f"asked for {ts}, but it is only {self._clock.now}")
        times = self._q["ts"].view()
        i = int(np.searchsorted(times, ts, side="right")) - 1
        if i < 0:
            return None
        bid, bq, ask, aq = (int(self._q[k].view()[i]) for k in ("bid", "bid_qty", "ask", "ask_qty"))
        return MarketData(int(times[i]), bid or None, bq, ask or None, aq)

    def __len__(self) -> int:
        return self._q["ts"].n


class _Clock:
    now = 0


class Context:
    """What a strategy can see and do. Deliberately small: no engine, no dataset."""

    __slots__ = ("_broker", "_clock", "view", "session_start", "session_end", "rng")

    def __init__(self, broker: Broker, clock: _Clock, view: MarketView, session_start: int, session_end: int,
                 rng: np.random.Generator) -> None:
        self._broker = broker
        self._clock = clock
        self.view = view
        self.session_start = session_start
        self.session_end = session_end
        self.rng = rng

    @property
    def now(self) -> int:
        return self._clock.now

    @property
    def position(self) -> int:
        return self._broker.position

    @property
    def cash(self) -> int:
        return self._broker.cash

    def open_orders(self) -> dict[int, int]:
        """Working orders: client id -> leaves quantity (as last reported)."""
        return self._broker.open_orders()

    def buy(self, qty: int, price: int | None = None, *, tif: mfengine.TimeInForce = mfengine.TimeInForce.GTC,
            post_only: bool = False) -> int:
        """A limit order at ``price``, or a market order without one. Returns the client id."""
        return self._broker.submit(mfengine.Side.BUY, qty, price, tif, post_only)

    def sell(self, qty: int, price: int | None = None, *, tif: mfengine.TimeInForce = mfengine.TimeInForce.GTC,
             post_only: bool = False) -> int:
        return self._broker.submit(mfengine.Side.SELL, qty, price, tif, post_only)

    def cancel(self, client_id: int) -> None:
        self._broker.cancel(client_id)

    def replace(self, client_id: int, price: int, qty: int) -> None:
        self._broker.replace(client_id, price, qty)

    def set_timer(self, interval_ns: int) -> None:
        """``on_timer`` every ``interval_ns`` from the session start, up to its end."""
        self._broker.set_timer(interval_ns)


class Strategy:
    """Base class: override the callbacks you need."""

    def on_start(self, ctx: Context) -> None: ...

    def on_market_data(self, ctx: Context, md: MarketData) -> None: ...

    def on_fill(self, ctx: Context, fill: StrategyFill) -> None: ...

    def on_order_update(self, ctx: Context, update: OrderUpdate) -> None: ...

    def on_timer(self, ctx: Context, t: int) -> None: ...

    def on_end(self, ctx: Context) -> None: ...
