"""The projection in PostgreSQL (M10): the same operations and cursor rule as
`MemoryStore`, in one transaction per batch. The cursor row is locked (SELECT ... FOR
UPDATE) and compared with the batch's start, so a batch is applied exactly once even if
the projector crashed after committing and re-reads it, or a second projector runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from services.db import models as m
from services.projector.projection import TABLES, CursorMismatch, Op

_TABLE = {"orders": m.orders, "fills": m.fills, "trades": m.trades, "positions": m.positions,
          "risk_limits": m.risk_limits, "sessions": m.sessions}
_KEYS = {"orders": ("order_id",), "fills": ("symbol", "trade_id", "order_id"), "trades": ("symbol", "trade_id"),
         "positions": ("account_id", "symbol"), "risk_limits": ("symbol", "scope", "account_id"),
         "sessions": ("symbol",)}


def _groups(ops: Sequence[Op]) -> list[tuple[tuple[Any, ...], list[Op]]]:
    """Consecutive operations of the same shape, so each group is one executemany."""
    out: list[tuple[tuple[Any, ...], list[Op]]] = []
    for op in ops:
        shape: tuple[Any, ...] = (op[0], tuple(sorted(op[2])) if op[0] == "order_update" else ())
        if out and out[-1][0] == shape:
            out[-1][1].append(op)
        else:
            out.append((shape, [op]))
    return out


class PgStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def cursor(self, symbol: str) -> int:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(m.projection_cursor.c.last_seq_no)
                                      .where(m.projection_cursor.c.symbol == symbol))).first()
        return int(row[0]) if row else 0

    async def apply(self, symbol: str, ops: Sequence[Op], *, expected: int, to: int) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(m.projection_cursor).values(symbol=symbol, last_seq_no=0)
                               .on_conflict_do_nothing())
            current = (await conn.execute(select(m.projection_cursor.c.last_seq_no)
                                          .where(m.projection_cursor.c.symbol == symbol).with_for_update())).scalar_one()
            if current != expected:
                raise CursorMismatch(f"{symbol}: cursor is {current}, batch starts at {expected}")
            for (kind, _), group in _groups(ops):
                await self._execute(conn, kind, group)
            await conn.execute(update(m.projection_cursor).where(m.projection_cursor.c.symbol == symbol)
                               .values(last_seq_no=to))

    async def _execute(self, conn: AsyncConnection, kind: str, ops: list[Op]) -> None:
        if kind == "order_insert":
            await conn.execute(insert(m.orders).on_conflict_do_nothing(), [op[1] for op in ops])
        elif kind == "order_update":
            fields = sorted(ops[0][2])
            stmt = update(m.orders).where(m.orders.c.order_id == bindparam("_oid")).values(
                {f: bindparam(f) for f in fields})
            await conn.execute(stmt, [{"_oid": op[1], **op[2]} for op in ops])
        elif kind in ("fill", "trade"):
            table = m.fills if kind == "fill" else m.trades
            await conn.execute(insert(table).on_conflict_do_nothing(), [op[1] for op in ops])
        elif kind == "position":
            pos = insert(m.positions)
            upsert = pos.on_conflict_do_update(
                index_elements=["account_id", "symbol"],
                set_={"qty": m.positions.c.qty + pos.excluded.qty, "cash": m.positions.c.cash + pos.excluded.cash,
                      "updated_seq": pos.excluded.updated_seq})
            for _, account, symbol, dq, dcash, seq in ops:  # in order: one account may appear twice
                await conn.execute(upsert, {"account_id": account, "symbol": symbol, "qty": dq, "cash": dcash,
                                          "updated_seq": seq})
        elif kind == "limit":
            lim = insert(m.risk_limits)
            upsert = lim.on_conflict_do_update(index_elements=["symbol", "scope", "account_id"],
                                               set_={"limits": lim.excluded.limits, "seq_no": lim.excluded.seq_no})
            for _, symbol, scope, account, limits, seq in ops:
                await conn.execute(upsert, {"symbol": symbol, "scope": scope, "account_id": account, "limits": limits,
                                          "seq_no": seq})
        elif kind == "session":
            ses = insert(m.sessions)
            upsert = ses.on_conflict_do_update(index_elements=["symbol"],
                                               set_={"state": ses.excluded.state, "seq_no": ses.excluded.seq_no})
            for _, symbol, state, seq in ops:
                await conn.execute(upsert, {"symbol": symbol, "state": state, "seq_no": seq})
        else:
            raise ValueError(f"unknown op {kind!r}")

    async def reset(self, symbol: str) -> None:
        """Forgets everything projected for one symbol (a rebuild starts at 0)."""
        async with self.engine.begin() as conn:
            for table in m.PROJECTION_TABLES:
                await conn.execute(delete(table).where(table.c.symbol == symbol))
            await conn.execute(delete(m.projection_cursor).where(m.projection_cursor.c.symbol == symbol))

    async def dump(self) -> dict[str, Any]:
        """Every projected row, in MemoryStore.dump()'s shape, from one consistent snapshot."""
        async with self.engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="REPEATABLE READ")
            cursors = {r.symbol: int(r.last_seq_no) for r in await conn.execute(select(m.projection_cursor))}
            out: dict[str, Any] = {"cursors": dict(sorted(cursors.items()))}
            for name in TABLES:
                rows = [{k: (int(v) if k == "cash" else v) for k, v in r.items()}
                        for r in (await conn.execute(select(_TABLE[name]))).mappings()]
                # Sorted here, by the same keys as MemoryStore (database collation may differ).
                out[name] = sorted(rows, key=lambda r: tuple(r[k] for k in _KEYS[name]))
        return out
