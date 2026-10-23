"""The Postgres projection's logic (M10), independent of any database.

`Projection.ops` turns projected records (journal record + the events a replay produces
+ the intake entry) into row operations. A store applies one batch of operations and
moves the symbol's cursor (the last projected seq_no) in one transaction, only if the
cursor is still where the batch started (`expected`). That makes projection idempotent
and safe against a second projector: a batch is applied exactly once or not at all.

`MemoryStore` is the reference store: the tests use it, and `python -m services.projector
verify` compares Postgres with a fresh in-memory projection of every journal.

Prices are ticks and quantities units, as integers. A position's `cash` is the sum of
signed fill notionals (ticks x qty: negative for buys), so PnL at mark price M is
`cash + qty * M`, exactly.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from services.matcher.intake import IntakeEntry

TABLES = ("orders", "fills", "trades", "positions", "risk_limits", "sessions")


class CursorMismatch(RuntimeError):
    """The batch does not start at the store's cursor: already applied, or another
    projector is running."""


@dataclass(frozen=True)
class ProjectedRecord:
    seq_no: int
    timestamp: int
    command: dict[str, Any]
    events: list[dict[str, Any]]
    intake: IntakeEntry


Op = tuple[Any, ...]


def _status(leaves: int, cum: int) -> str:
    if leaves == 0:
        return "filled"
    return "partially_filled" if cum else "open"


class Projection:
    @staticmethod
    def ops(symbol: str, records: Sequence[ProjectedRecord]) -> list[Op]:
        out: list[Op] = []
        for rec in records:
            out.extend(Projection._record(symbol, rec))
        return out

    @staticmethod
    def _record(symbol: str, rec: ProjectedRecord) -> Iterable[Op]:
        cmd, seq, ts = rec.command, rec.seq_no, rec.timestamp
        stamp = {"updated_seq": seq, "updated_ts": ts}
        if cmd["op"] == "new":
            first = next((e for e in rec.events if e["type"] in ("ack", "reject")), None)
            rejected = first is not None and first["type"] == "reject"
            yield ("order_insert", {
                "order_id": cmd["order_id"], "symbol": symbol, "account_id": cmd["account_id"],
                "client_order_id": rec.intake.key, "side": cmd["side"], "order_type": cmd["order_type"],
                "tif": cmd["tif"], "post_only": cmd["post_only"], "stp": cmd["stp"], "price": cmd["price"],
                "qty": cmd["qty"], "leaves_qty": 0 if rejected else cmd["qty"], "cum_qty": 0,
                "status": "rejected" if rejected else "open",
                "reject_reason": first["reason"] if rejected and first else None,
                "created_seq": seq, "created_ts": ts, **stamp})
        for e in rec.events:
            t = e["type"]
            if t == "fill":
                yield ("order_update", e["order_id"], {"leaves_qty": e["leaves_qty"], "cum_qty": e["cum_qty"],
                                                       "status": _status(e["leaves_qty"], e["cum_qty"]), **stamp})
                yield ("fill", {"symbol": symbol, "trade_id": e["trade_id"], "order_id": e["order_id"],
                                "account_id": e["account_id"], "side": e["side"], "liquidity": e["liquidity"],
                                "price": e["price"], "qty": e["qty"], "leaves_qty": e["leaves_qty"],
                                "cum_qty": e["cum_qty"], "seq_no": seq, "ts": ts})
                sign = 1 if e["side"] == "buy" else -1
                yield ("position", e["account_id"], symbol, sign * e["qty"], -sign * e["price"] * e["qty"], seq)
            elif t == "cancelled":
                yield ("order_update", e["order_id"], {"leaves_qty": 0, "status": "cancelled", **stamp})
            elif t == "replaced":
                cum = e["qty"] - e["leaves_qty"]
                yield ("order_update", e["order_id"], {"price": e["price"], "qty": e["qty"],
                                                       "leaves_qty": e["leaves_qty"],
                                                       "status": _status(e["leaves_qty"], cum), **stamp})
            elif t == "trade":
                yield ("trade", {"symbol": symbol, "trade_id": e["trade_id"], "price": e["price"], "qty": e["qty"],
                                 "maker_order_id": e["maker_order_id"], "taker_order_id": e["taker_order_id"],
                                 "aggressor_side": e["aggressor_side"], "seq_no": seq, "ts": ts})
            elif t == "session_changed":
                yield ("session", symbol, e["state"], seq)
            elif t == "admin_ack":
                if cmd["op"] == "instrument_limits":
                    yield ("limit", symbol, "instrument", 0, cmd["limits"], seq)
                elif cmd["op"] == "account_limits":
                    account = cmd["account_id"]
                    yield ("limit", symbol, "account" if account is not None else "default", account or 0,
                           cmd["limits"], seq)
                elif cmd["op"] == "kill_switch":
                    yield ("limit", symbol, "kill_switch", cmd["account_id"] or 0, {"engaged": cmd["engaged"]}, seq)


class MemoryStore:
    """The reference store: dicts, the same cursor rule as Postgres."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[Any, dict[str, Any]]] = {t: {} for t in TABLES}
        self.cursors: dict[str, int] = {}

    def cursor(self, symbol: str) -> int:
        return self.cursors.get(symbol, 0)

    def apply(self, symbol: str, ops: Sequence[Op], *, expected: int, to: int) -> None:
        if self.cursor(symbol) != expected:
            raise CursorMismatch(f"{symbol}: cursor is {self.cursor(symbol)}, batch starts at {expected}")
        for op in ops:
            self._apply(op)
        self.cursors[symbol] = to

    def _apply(self, op: Op) -> None:
        kind = op[0]
        t = self.tables
        if kind == "order_insert":
            t["orders"][op[1]["order_id"]] = dict(op[1])
        elif kind == "order_update":
            t["orders"][op[1]].update(op[2])
        elif kind == "fill":
            r = op[1]
            t["fills"][(r["symbol"], r["trade_id"], r["order_id"])] = dict(r)
        elif kind == "trade":
            r = op[1]
            t["trades"][(r["symbol"], r["trade_id"])] = dict(r)
        elif kind == "position":
            _, account, symbol, dq, dcash, seq = op
            row = t["positions"].setdefault((account, symbol), {"account_id": account, "symbol": symbol, "qty": 0,
                                                                "cash": 0, "updated_seq": 0})
            row["qty"] += dq
            row["cash"] += dcash
            row["updated_seq"] = seq
        elif kind == "limit":
            _, symbol, scope, account, limits, seq = op
            t["risk_limits"][(symbol, scope, account)] = {"symbol": symbol, "scope": scope, "account_id": account,
                                                          "limits": dict(limits), "seq_no": seq}
        elif kind == "session":
            _, symbol, state, seq = op
            t["sessions"][symbol] = {"symbol": symbol, "state": state, "seq_no": seq}
        else:
            raise ValueError(f"unknown op {kind!r}")

    def rows(self, table: str) -> list[dict[str, Any]]:
        return [self.tables[table][k] for k in sorted(self.tables[table])]

    def dump(self) -> dict[str, Any]:
        return {"cursors": dict(sorted(self.cursors.items())),
                **{name: copy.deepcopy(self.rows(name)) for name in TABLES}}
