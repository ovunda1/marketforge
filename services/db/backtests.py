"""Backtest run rows (M10), shared by the gateway (create, read) and the worker
(running, done, failed). Every write is keyed by the run id, so a retried job rewrites
the same rows instead of adding new ones."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from services.db.models import backtest_metrics, backtest_runs, equity_points


async def create(engine: AsyncEngine, run_id: str, account_id: int, config: dict[str, Any],
                 cap: int | None = None) -> bool:
    """Inserts a queued run; with `cap`, only if the account has fewer than `cap` runs
    queued or running (False otherwise). A transaction-scoped advisory lock per account
    serializes the count and the insert, so two concurrent requests cannot both pass."""
    async with engine.begin() as conn:
        if cap is not None:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": account_id})
            active = (await conn.execute(select(func.count()).select_from(backtest_runs).where(
                backtest_runs.c.account_id == account_id,
                backtest_runs.c.status.in_(("queued", "running"))))).scalar_one()
            if active >= cap:
                return False
        await conn.execute(insert(backtest_runs).values(id=run_id, account_id=account_id, config=config,
                                                        seed=config.get("seed"), status="queued"))
    return True


def _headline() -> list[Any]:
    """The few metrics a run list shows, read out of the JSONB document in SQL."""
    m = backtest_metrics.c.metrics
    return [m["performance"]["sharpe"].label("sharpe"), m["performance"]["dsr"].label("dsr"),
            m["performance"]["annual_return"].label("annual_return"), m["period"].label("period"),
            m["trials"].label("trials")]


async def list_runs(engine: AsyncEngine, limit: int) -> list[dict[str, Any]]:
    """Newest first, with headline metrics (not the whole document)."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(backtest_runs.c.id, backtest_runs.c.status, backtest_runs.c.config, backtest_runs.c.error,
                   backtest_runs.c.created_at, backtest_runs.c.finished_at, *_headline())
            .select_from(backtest_runs.outerjoin(backtest_metrics, backtest_metrics.c.run_id == backtest_runs.c.id))
            .order_by(backtest_runs.c.created_at.desc(), backtest_runs.c.id).limit(limit))
        out = []
        for r in rows.mappings():
            d = dict(r)
            for k in ("created_at", "finished_at"):
                d[k] = d[k].isoformat() if d[k] else None
            out.append(d)
        return out


async def counts(engine: AsyncEngine) -> dict[str, int]:
    """Runs by status, and the total: every run ever queued, failed ones included."""
    async with engine.connect() as conn:
        rows = await conn.execute(select(backtest_runs.c.status, func.count()).group_by(backtest_runs.c.status))
        by = {status: int(n) for status, n in rows}
    return {"total": sum(by.values()), **{s: by.get(s, 0) for s in ("queued", "running", "done", "failed")}}


async def get(engine: AsyncEngine, run_id: str) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = (await conn.execute(select(backtest_runs).where(backtest_runs.c.id == run_id))).mappings().first()
        if row is None:
            return None
        out = dict(row)
        metrics = (await conn.execute(select(backtest_metrics.c.metrics)
                                      .where(backtest_metrics.c.run_id == run_id))).scalar()
    for k in ("created_at", "started_at", "finished_at"):
        out[k] = out[k].isoformat() if out[k] else None
    out["metrics"] = metrics
    return out


async def equity(engine: AsyncEngine, run_id: str) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(equity_points).where(equity_points.c.run_id == run_id)
                                  .order_by(equity_points.c.idx))
        return [{k: v for k, v in r.items() if k != "run_id"} for r in rows.mappings()]


async def mark_running(engine: AsyncEngine, run_id: str) -> int:
    async with engine.begin() as conn:
        return int((await conn.execute(
            update(backtest_runs).where(backtest_runs.c.id == run_id)
            .values(status="running", attempts=backtest_runs.c.attempts + 1, started_at=func.now(), error=None)
            .returning(backtest_runs.c.attempts))).scalar_one())


async def mark_failed(engine: AsyncEngine, run_id: str, error: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(update(backtest_runs).where(backtest_runs.c.id == run_id)
                           .values(status="failed", error=error[:4000], finished_at=func.now()))


async def finish(engine: AsyncEngine, run_id: str, metrics: dict[str, Any], points: list[dict[str, Any]],
                 commit: str | None) -> None:
    async with engine.begin() as conn:
        stmt = pg_insert(backtest_metrics).values(run_id=run_id, metrics=metrics)
        await conn.execute(stmt.on_conflict_do_update(index_elements=["run_id"], set_={"metrics": stmt.excluded.metrics}))
        await conn.execute(delete(equity_points).where(equity_points.c.run_id == run_id))
        if points:
            await conn.execute(insert(equity_points), [{"run_id": run_id, **p} for p in points])
        await conn.execute(update(backtest_runs).where(backtest_runs.c.id == run_id)
                           .values(status="done", commit=commit, finished_at=func.now()))
