"""Replaying recorded L1 quotes and trades into the engine (M8).

The data has no order ids, so the replay keeps its own orders that reproduce it:

- **Quotes.** Each side's displayed size is held by orders of ``LIQUIDITY_ACCOUNT`` at the
  displayed price. On each quote row the replay reconciles them with the data: a new
  price cancels the old level and posts the new one; more size adds an order at the back
  of the queue; less size shrinks orders, which keeps their time priority. The orders are
  post-only, so a quote never trades with the strategy: if the strategy's order sits
  where the data's quote moves to, the historical level is left out of the book
  (in the data nothing traded there).
- **Trades.** Each print becomes an IOC from ``TAPE_ACCOUNT``: the aggressor's side,
  limited at the printed price, for the printed size. It fills the strategy's orders
  that are better priced, or ahead in the queue, before historical liquidity.

Two assumptions bias results toward the strategy when made optimistically, so the
conservative choice is the default and the other is opt-in (``EventConfig``):

- ``queue``: when displayed size shrinks without a trade, whose orders were cancelled?
  ``pessimistic`` (default): the newest first, i.e. the ones behind the strategy, so
  the strategy keeps its place but the queue ahead of it does not shrink. ``optimistic``:
  the oldest first, so the strategy moves up.
- ``consumed_liquidity``: the data does not know the strategy took liquidity, and keeps
  showing it. ``stays_removed`` (default): what the strategy took at a price stays
  taken until the level clears (price changes or the side empties). ``restore``: the next
  quote update puts it back.

Only the top level exists (L1 data): a sweep past it finds nothing.
"""

from __future__ import annotations

from typing import Any

import mfengine
import numpy as np
import pandas as pd

from research.backtest.loop import Priority, Simulation
from research.data import schema

LIQUIDITY_ACCOUNT = 1
TAPE_ACCOUNT = 2

QUEUE_MODELS = ("pessimistic", "optimistic")
CONSUMED_MODELS = ("stays_removed", "restore")


class _Order:
    __slots__ = ("oid", "total", "leaves")

    def __init__(self, oid: int, qty: int) -> None:
        self.oid, self.total, self.leaves = oid, qty, qty


class _Level:
    """The replay's orders on one side: one price, oldest first."""

    def __init__(self, side: mfengine.Side) -> None:
        self.side = side
        self.price: int | None = None
        self.orders: list[_Order] = []
        self.consumed = 0  # taken by the strategy at this price

    def size(self) -> int:
        return sum(o.leaves for o in self.orders)


class HistoricalFlow:
    def __init__(self, sim: Simulation, quotes: pd.DataFrame, trades: pd.DataFrame, *, queue: str,
                 consumed_liquidity: str) -> None:
        schema.validate(quotes, "quotes")
        schema.validate(trades, "trades")
        self.sim = sim
        self.queue = queue
        self.restore = consumed_liquidity == "restore"
        self.levels = {mfengine.Side.BUY: _Level(mfengine.Side.BUY), mfengine.Side.SELL: _Level(mfengine.Side.SELL)}
        self._by_oid: dict[int, tuple[_Level, _Order]] = {}
        self._quotes = quotes[["ts", "bid_px", "bid_qty", "ask_px", "ask_qty"]].to_numpy(np.int64)
        self._trades = trades[["ts", "price", "qty", "aggressor"]].to_numpy(np.int64)
        # One merged sequence: by time, trades before quotes at the same time (a quote
        # at that time already shows the trade's effect), file order within each.
        ts = np.concatenate([self._trades[:, 0], self._quotes[:, 0]])
        kind = np.concatenate([np.zeros(len(self._trades), np.int64), np.ones(len(self._quotes), np.int64)])
        row = np.concatenate([np.arange(len(self._trades)), np.arange(len(self._quotes))])
        order = np.lexsort((row, kind, ts))
        self._seq_ts, self._seq_kind, self._seq_row = ts[order], kind[order], row[order]
        self._next = 0

    @property
    def start(self) -> int:
        return int(self._seq_ts[0]) if len(self._seq_ts) else 0

    @property
    def end(self) -> int:
        return int(self._seq_ts[-1]) if len(self._seq_ts) else 0

    def schedule(self) -> None:
        if self._next < len(self._seq_ts):
            i = self._next
            priority = Priority.TRADE if self._seq_kind[i] == 0 else Priority.QUOTE
            self.sim.at(int(self._seq_ts[i]), priority, self._step)

    def _step(self) -> None:
        i = self._next
        self._next += 1
        if self._seq_kind[i] == 0:
            self._trade(self._trades[self._seq_row[i]])
        else:
            self._quote(self._quotes[self._seq_row[i]])
        self.schedule()

    # --- trades -------------------------------------------------------------------------

    def _trade(self, row: np.ndarray) -> None:
        _, price, qty, aggressor = (int(v) for v in row)
        side = mfengine.Side.BUY if aggressor == schema.BUY else mfengine.Side.SELL
        self.sim.process(mfengine.NewOrder(seq_no=0, order_id=self.sim.new_order_id(self), account_id=TAPE_ACCOUNT,
                                           side=side, qty=qty, price=price, tif=mfengine.TimeInForce.IOC))

    # --- quotes --------------------------------------------------------------------------

    def _quote(self, row: np.ndarray) -> None:
        _, bid, bid_qty, ask, ask_qty = (int(v) for v in row)
        wanted = {mfengine.Side.BUY: (bid or None, bid_qty), mfengine.Side.SELL: (ask or None, ask_qty)}
        # Take away first on both sides, then add: the old ask must be gone before a bid
        # that moved up to it is posted.
        targets = {side: self._shrink(self.levels[side], *wanted[side]) for side in wanted}
        for side, target in targets.items():
            level = self.levels[side]
            if level.price is not None and target > level.size():
                self._post(level, target - level.size())

    def _shrink(self, level: _Level, price: int | None, qty: int) -> int:
        """Cancel a level that moved or emptied, shrink one that is too big. Returns the
        size the level should have."""
        if price != level.price:
            for o in list(level.orders):
                self.sim.process(mfengine.Cancel(seq_no=0, order_id=o.oid))
            level.price, level.consumed = price, 0
            return qty if price is not None else 0
        target = qty if self.restore else max(0, qty - level.consumed)
        excess = level.size() - target
        orders = level.orders if self.queue == "optimistic" else list(reversed(level.orders))
        for o in list(orders):
            if excess <= 0:
                break
            if o.leaves <= excess:
                excess -= o.leaves
                self.sim.process(mfengine.Cancel(seq_no=0, order_id=o.oid))
            else:
                filled = o.total - o.leaves
                self.sim.process(mfengine.Replace(seq_no=0, order_id=o.oid, price=level.price,
                                                  qty=filled + o.leaves - excess))
                excess = 0
        return target

    def _post(self, level: _Level, qty: int) -> None:
        oid = self.sim.new_order_id(self)
        order = _Order(oid, qty)
        level.orders.append(order)
        self._by_oid[oid] = (level, order)
        self.sim.process(mfengine.NewOrder(seq_no=0, order_id=oid, account_id=LIQUIDITY_ACCOUNT, side=level.side,
                                           qty=qty, price=level.price, post_only=True))

    # --- engine -> replay state ------------------------------------------------------------

    def _drop(self, oid: int) -> None:
        level, order = self._by_oid.pop(oid)
        level.orders.remove(order)

    def on_events(self, sim: Simulation, events: list[Any]) -> None:
        for e in events:
            if isinstance(e, mfengine.Trade):
                maker = self._by_oid.get(e.maker_order_id)
                # Liquidity taken by someone other than the tape: the strategy.
                if maker is not None and sim.owner(e.taker_order_id) is not self:
                    level = maker[0]
                    if e.price == level.price:
                        level.consumed += e.qty
                continue
            if e.order_id not in self._by_oid:
                continue  # a tape order
            if isinstance(e, mfengine.Fill):
                order = self._by_oid[e.order_id][1]
                order.leaves = e.leaves_qty
                if e.leaves_qty == 0:
                    self._drop(e.order_id)
            elif isinstance(e, mfengine.Replaced):
                order = self._by_oid[e.order_id][1]
                order.leaves = e.leaves_qty
                order.total = e.qty
            elif isinstance(e, (mfengine.Cancelled, mfengine.Reject)):
                self._drop(e.order_id)
