"""Intraday order-book momentum (SPEC strategy 3), event-driven through the engine.

On every top-of-book update: the mid's return over the last ``horizon_ns`` in basis
points, plus the size imbalance at the touch, ``(bid_qty - ask_qty) / (bid_qty + ask_qty)``:

    score = return_weight * return_bps + imbalance_weight * imbalance

Above ``threshold`` it buys ``qty`` with an IOC at the ask, below ``-threshold`` it sells at
the bid, up to ``max_position``. A position is closed when the score turns against it,
after ``max_hold_ns``, and from ``flatten_before_end_ns`` before the session end, when no
new position is opened. One order is in flight at a time.
"""

from __future__ import annotations

import mfengine

from research.backtest.strategy import Context, MarketData, OrderUpdate, Strategy, StrategyFill

IOC = mfengine.TimeInForce.IOC


class OrderBookMomentum(Strategy):
    def __init__(self, horizon_ns: int, return_weight: float = 1.0, imbalance_weight: float = 1.0,
                 threshold: float = 1.0, qty: int = 1, max_position: int = 10, max_hold_ns: int | None = None,
                 flatten_before_end_ns: int = 0) -> None:
        if horizon_ns <= 0 or qty <= 0 or max_position < qty or threshold < 0:
            raise ValueError("invalid order-book momentum parameters")
        self.horizon_ns = horizon_ns
        self.return_weight = return_weight
        self.imbalance_weight = imbalance_weight
        self.threshold = threshold
        self.qty = qty
        self.max_position = max_position
        self.max_hold_ns = max_hold_ns
        self.flatten_before_end_ns = flatten_before_end_ns
        self._in_flight: int | None = None
        self._opened_at: int | None = None

    def score(self, mid_now: float, mid_then: float, bid_qty: int, ask_qty: int) -> float:
        ret_bps = (mid_now - mid_then) / mid_then * 10_000
        imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty)
        return self.return_weight * ret_bps + self.imbalance_weight * imbalance

    def _trade(self, ctx: Context, signed_qty: int, md: MarketData) -> None:
        if signed_qty > 0:
            self._in_flight = ctx.buy(signed_qty, price=md.ask, tif=IOC)
        else:
            self._in_flight = ctx.sell(-signed_qty, price=md.bid, tif=IOC)

    def on_market_data(self, ctx: Context, md: MarketData) -> None:
        if self._in_flight is not None or md.bid is None or md.ask is None:
            return
        pos = ctx.position
        if ctx.now >= ctx.session_end - self.flatten_before_end_ns:
            if pos:
                self._trade(ctx, -pos, md)
            return
        if pos and self.max_hold_ns is not None and self._opened_at is not None \
                and ctx.now - self._opened_at >= self.max_hold_ns:
            self._trade(ctx, -pos, md)
            return
        then = ctx.view.asof(ctx.now - self.horizon_ns)
        if then is None or then.bid is None or then.ask is None:
            return
        s = self.score((md.bid + md.ask) / 2, (then.bid + then.ask) / 2, md.bid_qty, md.ask_qty)
        if s > self.threshold and pos + self.qty <= self.max_position:
            self._trade(ctx, self.qty, md)
        elif s < -self.threshold and pos - self.qty >= -self.max_position:
            self._trade(ctx, -self.qty, md)
        elif (pos > 0 and s < 0) or (pos < 0 and s > 0):
            self._trade(ctx, -pos, md)

    def on_fill(self, ctx: Context, fill: StrategyFill) -> None:
        if ctx.position == 0:
            self._opened_at = None
        elif self._opened_at is None:
            self._opened_at = fill.ts
        if fill.client_id == self._in_flight and fill.leaves_qty == 0:
            self._in_flight = None

    def on_order_update(self, ctx: Context, update: OrderUpdate) -> None:
        if update.client_id == self._in_flight and update.kind in ("reject", "cancelled"):
            self._in_flight = None
