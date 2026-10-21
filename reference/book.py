"""The reference order book: a flat list of resting orders and linear scans.

Written to be obviously correct rather than fast. There are no price levels, no linked
lists and no index: every decision re-derives the answer from the list of resting
orders. Time priority is the seq_no of the command that last set it, so "FIFO within a
price" is just a sort by (price, priority). Rules: docs/order-types.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from reference.commands import Cancel, Command, NewOrder, Replace


@dataclass
class Resting:
    order_id: int
    account: int
    side: str
    price: int
    qty: int  # total quantity (a replace changes it)
    filled: int
    priority: int  # seq_no that last set its time priority; lower is earlier
    post_only: bool
    stp: str

    @property
    def leaves(self) -> int:
        return self.qty - self.filled


@dataclass
class Taker:
    """An incoming order, or a resting order re-priced by a replace."""

    seq: int
    order_id: int
    account: int
    side: str
    limit: int | None  # None for a market order
    post_only: bool
    stp: str
    leaves: int
    filled: int


def _crosses(side: str, limit: int | None, resting_price: int) -> bool:
    if limit is None:
        return True
    return resting_price <= limit if side == "B" else resting_price >= limit


class ReferenceBook:
    def __init__(self) -> None:
        self.orders: list[Resting] = []
        self.next_trade_id = 1
        # Ids of every acked order: unique for the session, even after the order is gone.
        # A rejected order never used its id. (Reset at session open from M5.)
        self.used_ids: set[int] = set()

    # --- Commands -----------------------------------------------------------------

    def process(self, command: Command) -> list[str]:
        """Applies one command and returns its canonical event lines."""
        out: list[str] = []
        if isinstance(command, NewOrder):
            self._new_order(command, out)
        elif isinstance(command, Cancel):
            self._cancel(command, out)
        else:
            self._replace(command, out)
        return out

    def _new_order(self, o: NewOrder, out: list[str]) -> None:
        is_market = o.type == "MKT"
        reason = None
        if (is_market and o.price != 0) or (not is_market and o.price <= 0):
            reason = "INVALID_PRICE"
        elif o.qty <= 0:
            reason = "INVALID_QTY"
        elif is_market and (o.tif != "IOC" or o.post_only):
            reason = "INVALID_ORDER_TYPE"
        elif o.post_only and o.tif != "GTC":
            reason = "INVALID_ORDER_TYPE"
        elif o.order_id in self.used_ids:
            reason = "DUPLICATE_ORDER_ID"
        elif is_market and not any(r.side != o.side for r in self.orders):
            reason = "NO_LIQUIDITY"
        elif o.post_only and self._reachable(o.side, o.price):
            reason = "POST_ONLY_WOULD_CROSS"
        elif o.tif == "FOK" and not self._fok_fillable(o):
            reason = "FOK_NOT_FILLABLE"
        if reason is not None:
            out.append(f"REJ seq={o.seq} id={o.order_id} reason={reason}")
            return

        self.used_ids.add(o.order_id)
        out.append(
            f"ACK seq={o.seq} id={o.order_id} side={o.side} type={o.type} tif={o.tif} "
            f"po={int(o.post_only)} stp={o.stp} px={o.price} qty={o.qty}"
        )
        taker = Taker(
            seq=o.seq, order_id=o.order_id, account=o.account, side=o.side,
            limit=None if is_market else o.price, post_only=o.post_only, stp=o.stp,
            leaves=o.qty, filled=0,
        )
        self._execute(taker, o.tif, o.qty, out)

    def _cancel(self, c: Cancel, out: list[str]) -> None:
        order = self._find(c.order_id)
        if order is None:
            out.append(f"REJ seq={c.seq} id={c.order_id} reason=UNKNOWN_ORDER")
            return
        self._remove(order)
        out.append(f"CXL seq={c.seq} id={c.order_id} qty={order.leaves} reason=USER")

    def _replace(self, r: Replace, out: list[str]) -> None:
        order = self._find(r.order_id)
        reason = None
        if r.price <= 0:
            reason = "INVALID_PRICE"
        elif r.qty <= 0:
            reason = "INVALID_QTY"
        elif order is None:
            reason = "UNKNOWN_ORDER"
        elif r.qty <= order.filled:
            reason = "REPLACE_QTY_TOO_LOW"
        elif r.price != order.price and order.post_only and self._reachable(order.side, r.price):
            reason = "POST_ONLY_WOULD_CROSS"
        if reason is not None:
            out.append(f"REJ seq={r.seq} id={r.order_id} reason={reason}")
            return
        assert order is not None

        leaves = r.qty - order.filled
        if r.price == order.price:
            keep = r.qty <= order.qty
            order.qty = r.qty
            if not keep:
                order.priority = r.seq  # to the back of its price
            prio = "KEPT" if keep else "LOST"
            out.append(f"RPL seq={r.seq} id={r.order_id} px={r.price} qty={r.qty} leaves={leaves} prio={prio}")
            return

        # Price change: cancel + new, keeping id, filled quantity and STP mode.
        out.append(f"RPL seq={r.seq} id={r.order_id} px={r.price} qty={r.qty} leaves={leaves} prio=LOST")
        self._remove(order)
        taker = Taker(
            seq=r.seq, order_id=order.order_id, account=order.account, side=order.side,
            limit=r.price, post_only=order.post_only, stp=order.stp,
            leaves=leaves, filled=order.filled,
        )
        self._execute(taker, "GTC", r.qty, out)

    # --- Matching -----------------------------------------------------------------

    def _execute(self, t: Taker, tif: str, total_qty: int, out: list[str]) -> None:
        stp_cancelled = self._match(t, out)
        if t.leaves == 0:
            return
        if stp_cancelled:
            out.append(f"CXL seq={t.seq} id={t.order_id} qty={t.leaves} reason=SELF_TRADE")
        elif tif != "GTC":
            out.append(f"CXL seq={t.seq} id={t.order_id} qty={t.leaves} reason=UNFILLED")
        else:
            assert t.limit is not None
            self.orders.append(
                Resting(
                    order_id=t.order_id, account=t.account, side=t.side, price=t.limit,
                    qty=total_qty, filled=t.filled, priority=t.seq,
                    post_only=t.post_only, stp=t.stp,
                )
            )

    def _queue(self, side: str, limit: int | None) -> list[Resting]:
        """Opposite-side orders an aggressor on `side` can reach, in priority order."""
        reachable = [r for r in self.orders if r.side != side and _crosses(side, limit, r.price)]
        if side == "B":
            return sorted(reachable, key=lambda r: (r.price, r.priority))
        return sorted(reachable, key=lambda r: (-r.price, r.priority))

    def _reachable(self, side: str, price: int) -> bool:
        return bool(self._queue(side, price))

    def _match(self, t: Taker, out: list[str]) -> bool:
        """Sweeps one maker at a time. Returns True if STP cancelled the taker."""
        while t.leaves > 0:
            queue = self._queue(t.side, t.limit)
            if not queue:
                return False
            maker = queue[0]
            if maker.account == t.account:
                if t.stp == "CN":
                    return True
                out.append(f"CXL seq={t.seq} id={maker.order_id} qty={maker.leaves} reason=SELF_TRADE")
                self._remove(maker)
                if t.stp == "CB":
                    return True
                continue

            qty = min(t.leaves, maker.leaves)
            trade_id = self.next_trade_id
            self.next_trade_id += 1
            maker.filled += qty
            t.leaves -= qty
            t.filled += qty
            px = maker.price
            out.append(
                f"TRD seq={t.seq} trade={trade_id} px={px} qty={qty} "
                f"maker={maker.order_id} taker={t.order_id} aggr={t.side}"
            )
            out.append(
                f"FILL seq={t.seq} trade={trade_id} id={maker.order_id} acct={maker.account} "
                f"side={maker.side} liq=M px={px} qty={qty} leaves={maker.leaves} cum={maker.filled}"
            )
            out.append(
                f"FILL seq={t.seq} trade={trade_id} id={t.order_id} acct={t.account} "
                f"side={t.side} liq=T px={px} qty={qty} leaves={t.leaves} cum={t.filled}"
            )
            if maker.leaves == 0:
                self._remove(maker)
        return False

    def _fok_fillable(self, o: NewOrder) -> bool:
        """Would the sweep fill the FOK completely? Same walk as _match, no changes."""
        available = 0
        for r in self._queue(o.side, o.price):
            if r.account == o.account:
                if o.stp == "CO":
                    continue  # the sweep would cancel it and go on
                return False  # the sweep would stop here
            available += r.leaves
            if available >= o.qty:
                return True
        return False

    # --- Helpers and output -------------------------------------------------------

    def _find(self, order_id: int) -> Resting | None:
        for r in self.orders:
            if r.order_id == order_id:
                return r
        return None

    def _remove(self, order: Resting) -> None:
        self.orders = [r for r in self.orders if r is not order]

    def _side_top(self, side: str) -> str:
        prices = [r.price for r in self.orders if r.side == side]
        if not prices:
            return "-"
        best = max(prices) if side == "B" else min(prices)
        qty = sum(r.leaves for r in self.orders if r.side == side and r.price == best)
        return f"{best}x{qty}"

    def top_of_book(self, seq: int) -> str:
        return f"BOOK seq={seq} bid={self._side_top('B')} ask={self._side_top('S')}"

    def depth(self) -> list[str]:
        lines = []
        for side, descending in (("B", True), ("S", False)):
            prices = sorted({r.price for r in self.orders if r.side == side}, reverse=descending)
            for px in prices:
                at = sorted((r for r in self.orders if r.side == side and r.price == px), key=lambda r: r.priority)
                orders = ",".join(f"{r.order_id}:{r.leaves}" for r in at)
                lines.append(f"LVL side={side} px={px} qty={sum(r.leaves for r in at)} orders={orders}")
        return lines
