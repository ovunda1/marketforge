"""Market data for one symbol (M10): a snapshot of the top `depth` levels per side, then
one update per batch that changed the book or traded, numbered md_seq 1, 2, 3... within
a matcher epoch. An update carries the levels that changed in the published window
(qty 0 removes a level), the trades and the top of book. A client applies updates in
order; a gap in md_seq or a new epoch means it must take a new snapshot."""

from __future__ import annotations

from typing import Any

import mfengine as mf

Level = tuple[int, int, int]  # price, total qty, order count


class BookFeed:
    def __init__(self, symbol: str, depth: int) -> None:
        self.symbol = symbol
        self.depth = depth
        self.epoch = 0
        self.md_seq = 0
        self.seq_no = 0
        self._bids: dict[int, Level] = {}
        self._asks: dict[int, Level] = {}

    def reset(self, engine: mf.Engine, epoch: int) -> None:
        """Start of an epoch (a matcher start): md_seq restarts at 0."""
        self.epoch = epoch
        self.md_seq = 0
        self.seq_no = engine.last_seq_no or 0
        self._bids, self._asks = self._levels(engine)

    def _levels(self, engine: mf.Engine) -> tuple[dict[int, Level], dict[int, Level]]:
        return ({lv[0]: tuple(lv) for lv in engine.depth(mf.Side.BUY, self.depth)},  # type: ignore[misc]
                {lv[0]: tuple(lv) for lv in engine.depth(mf.Side.SELL, self.depth)})  # type: ignore[misc]

    def snapshot(self) -> dict[str, Any]:
        return {"type": "snapshot", "symbol": self.symbol, "epoch": self.epoch, "md_seq": self.md_seq,
                "seq_no": self.seq_no, "depth": self.depth,
                "bids": [list(lv) for lv in sorted(self._bids.values(), key=lambda x: -x[0])],
                "asks": [list(lv) for lv in sorted(self._asks.values(), key=lambda x: x[0])]}

    @staticmethod
    def _diff(old: dict[int, Level], new: dict[int, Level], reverse: bool) -> list[list[int]]:
        changed = [list(lv) for p, lv in new.items() if old.get(p) != lv]
        changed += [[p, 0, 0] for p in old if p not in new]
        return sorted(changed, key=lambda x: -x[0] if reverse else x[0])

    def after_batch(self, engine: mf.Engine, trades: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The update for this batch's commands, or None if nothing visible changed."""
        bids, asks = self._levels(engine)
        db, da = self._diff(self._bids, bids, True), self._diff(self._asks, asks, False)
        self.seq_no = engine.last_seq_no or 0
        if not db and not da and not trades:
            return None
        self._bids, self._asks = bids, asks
        self.md_seq += 1
        best_bid = max(bids) if bids else None
        best_ask = min(asks) if asks else None
        return {"type": "update", "symbol": self.symbol, "epoch": self.epoch, "md_seq": self.md_seq,
                "seq_no": self.seq_no, "bids": db, "asks": da,
                "trades": [{k: t[k] for k in ("seq_no", "trade_id", "price", "qty", "aggressor_side")} for t in trades],
                "l1": {"bid": best_bid, "bid_qty": bids[best_bid][1] if best_bid is not None else 0,
                       "ask": best_ask, "ask_qty": asks[best_ask][1] if best_ask is not None else 0}}
