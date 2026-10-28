"""The gateway's real dependencies (M10): Redis for the matcher round trip, the book
cache, rate limits and fan-out; Postgres (the projection) for reads; arq for backtests.

`RedisMatcherClient`: each API worker process has its own reply stream
(`mf:reply:<worker id>`) and one reader task that resolves a future per request id.
A request is an XADD to `mf:cmd`; its deadline is now + the request timeout, and the
matcher drops it unprocessed once that passes, so a timed-out request is either
journaled before its deadline or never.

`Subscriptions`: one Redis pub/sub connection per worker, fanned out through the `Hub`.
Market data uses a pattern subscription (md:*); execution and backtest channels are
subscribed while some client wants them. If the connection drops, every client is told
to resnapshot (it may have missed updates).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.deps import Account, Deps, MatcherTimeout
from services.api.hub import Hub
from services.common import keys
from services.common.config import Settings
from services.common.protocol import Reply, Request
from services.db import backtests
from services.db.engine import make_engine
from services.db.models import accounts, fills, orders, positions, projection_cursor, trades

log = logging.getLogger("mf.api")


class RedisMatcherClient:
    def __init__(self, redis: Redis, timeout_s: float) -> None:
        self.redis = redis
        self.timeout_s = timeout_s
        self.worker_id = uuid.uuid4().hex
        self.reply_to = keys.reply_stream(self.worker_id)
        self._pending: dict[str, asyncio.Future[Reply]] = {}
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._read())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self.redis.delete(self.reply_to)

    async def _read(self) -> None:
        last = "$"
        while True:
            try:
                resp = await self.redis.xread({self.reply_to: last}, block=1000, count=1000)
            except RedisConnectionError:
                await asyncio.sleep(0.2)
                continue
            for _, entries in resp or []:
                for entry_id, fields in entries:
                    last = entry_id
                    reply = Reply.from_json(fields["m"])
                    fut = self._pending.pop(reply.request_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(reply)

    async def request(self, req: Request) -> Reply:
        req = Request(**{**req.__dict__, "reply_to": self.reply_to,
                         "deadline_ns": time.time_ns() + int(self.timeout_s * 1e9)})
        fut: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        self._pending[req.request_id] = fut
        try:
            await self.redis.xadd(keys.COMMANDS, {"m": req.to_json()}, maxlen=100_000, approximate=True)
            # A little longer than the deadline: a reply for a request the matcher took just
            # before its deadline can still arrive.
            return await asyncio.wait_for(fut, self.timeout_s + 0.5)
        except (TimeoutError, RedisConnectionError):
            raise MatcherTimeout() from None
        finally:
            self._pending.pop(req.request_id, None)


class PgStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def account_by_prefix(self, prefix: str) -> Account | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(accounts.c.id, accounts.c.role, accounts.c.api_key_hash,
                                             accounts.c.practice)
                                      .where(accounts.c.api_key_prefix == prefix))).first()
        return Account(int(row.id), row.role, row.api_key_hash, bool(row.practice)) if row else None

    async def create_account(self, row: dict[str, Any]) -> int:
        async with self.engine.begin() as conn:
            return int((await conn.execute(insert(accounts).values(**row).returning(accounts.c.id))).scalar_one())

    async def wait_for(self, symbol: str, seq_no: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            async with self.engine.connect() as conn:
                cur = (await conn.execute(select(projection_cursor.c.last_seq_no)
                                          .where(projection_cursor.c.symbol == symbol))).scalar()
            if cur is not None and cur >= seq_no:
                return True
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.02)

    async def get_order(self, order_id: int) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(orders).where(orders.c.order_id == order_id))).mappings().first()
            if row is None:
                return None
            fs = (await conn.execute(select(fills.c.trade_id, fills.c.price, fills.c.qty, fills.c.liquidity,
                                            fills.c.seq_no).where(fills.c.order_id == order_id)
                                     .order_by(fills.c.trade_id))).mappings()
            return {**dict(row), "type": row["order_type"], "seq_no": row["updated_seq"],
                    "fills": [dict(f) for f in fs]}

    async def list_orders(self, account_id: int, open_only: bool, symbol: str | None,
                          limit: int) -> list[dict[str, Any]]:
        q = select(orders).where(orders.c.account_id == account_id)
        if open_only:
            q = q.where(orders.c.status.in_(("open", "partially_filled")))
        if symbol is not None:
            q = q.where(orders.c.symbol == symbol)
        async with self.engine.connect() as conn:
            rows = await conn.execute(q.order_by(orders.c.order_id.desc()).limit(limit))
            return [{**dict(r), "type": r["order_type"], "seq_no": r["updated_seq"]} for r in rows.mappings()]

    async def fills(self, account_id: int, symbol: str | None, limit: int) -> list[dict[str, Any]]:
        q = select(fills).where(fills.c.account_id == account_id)
        if symbol is not None:
            q = q.where(fills.c.symbol == symbol)
        async with self.engine.connect() as conn:
            rows = await conn.execute(q.order_by(fills.c.seq_no.desc(), fills.c.trade_id.desc()).limit(limit))
            return [dict(r) for r in rows.mappings()]

    async def trades(self, symbol: str, limit: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(trades).where(trades.c.symbol == symbol)
                                      .order_by(trades.c.trade_id.desc()).limit(limit))
            return [dict(r) for r in rows.mappings()]

    async def positions(self, account_id: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            pos = {r.symbol: {"symbol": r.symbol, "qty": int(r.qty), "cash": int(r.cash)}
                   for r in await conn.execute(select(positions).where(positions.c.account_id == account_id))}
            open_rows = await conn.execute(
                select(orders.c.symbol, orders.c.side, func.sum(orders.c.leaves_qty).label("qty"),
                       func.sum(orders.c.leaves_qty * orders.c.price).label("notional"))
                .where(orders.c.account_id == account_id, orders.c.status.in_(("open", "partially_filled")))
                .group_by(orders.c.symbol, orders.c.side))
            for r in open_rows:
                p = pos.setdefault(r.symbol, {"symbol": r.symbol, "qty": 0, "cash": 0})
                p[f"open_{r.side}_qty"] = int(r.qty)
                p["open_notional"] = p.get("open_notional", 0) + int(r.notional)
            for symbol, p in pos.items():
                mark = (await conn.execute(select(trades.c.price).where(trades.c.symbol == symbol)
                                           .order_by(trades.c.trade_id.desc()).limit(1))).scalar()
                p["mark"] = int(mark) if mark is not None else None
                p.setdefault("open_buy_qty", 0)
                p.setdefault("open_sell_qty", 0)
                p.setdefault("open_notional", 0)
        return [pos[s] for s in sorted(pos)]

    async def create_backtest(self, run_id: str, account_id: int, config: dict[str, Any],
                              cap: int | None = None) -> bool:
        return await backtests.create(self.engine, run_id, account_id, config, cap)

    async def list_backtests(self, limit: int) -> list[dict[str, Any]]:
        return await backtests.list_runs(self.engine, limit)

    async def backtest_counts(self) -> dict[str, int]:
        return await backtests.counts(self.engine)

    async def get_backtest(self, run_id: str) -> dict[str, Any] | None:
        return await backtests.get(self.engine, run_id)

    async def equity(self, run_id: str) -> list[dict[str, Any]]:
        return await backtests.equity(self.engine, run_id)


class RedisBookCache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def get_book(self, symbol: str) -> dict[str, Any] | None:
        raw = await self.redis.get(keys.book(symbol))
        return json.loads(raw) if raw else None


class RedisCounter:
    """INCR with an expiry set on the first hit (M11: practice accounts per client IP)."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def incr(self, key: str, ttl_s: int) -> int:
        pipe = self.redis.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, ttl_s, nx=True)
        count, _ = await pipe.execute()
        return int(count)


class RedisRateLimiter:
    """A fixed one-second window per account (INCR + EXPIRE). The engine has its own
    token bucket per account, on journal time; this one protects the gateway."""

    def __init__(self, redis: Redis, per_s: int) -> None:
        self.redis = redis
        self.per_s = per_s

    async def allow(self, account_id: int) -> bool:
        key = keys.rate(account_id, int(time.time()))
        pipe = self.redis.pipeline(transaction=False)
        pipe.incr(key)
        pipe.expire(key, 2)
        count, _ = await pipe.execute()
        return int(count) <= self.per_s


class ArqQueue:
    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.pool: Any = None

    async def start(self) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        self.pool = await create_pool(RedisSettings.from_dsn(self.redis_url))

    async def stop(self) -> None:
        if self.pool is not None:
            await self.pool.aclose()

    async def enqueue(self, run_id: str) -> None:
        await self.pool.enqueue_job("run_backtest", run_id, _job_id=run_id)


class Subscriptions:
    def __init__(self, redis: Redis, hub: Hub) -> None:
        self.redis = redis
        self.hub = hub
        self.pubsub: Any = None
        self._task: asyncio.Task[None] | None = None
        hub.on_first = self._subscribe
        hub.on_last = self._unsubscribe

    def _subscribe(self, channel: str) -> None:
        if not channel.startswith("md:") and self.pubsub is not None:
            asyncio.create_task(self.pubsub.subscribe(channel))

    def _unsubscribe(self, channel: str) -> None:
        if not channel.startswith("md:") and self.pubsub is not None:
            asyncio.create_task(self.pubsub.unsubscribe(channel))

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        first = True
        while True:
            try:
                self.pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
                await self.pubsub.psubscribe("md:*")
                others = [c for c in self.hub.channels() if not c.startswith("md:")]
                if others:
                    await self.pubsub.subscribe(*others)
                if not first:
                    self.hub.resnapshot_all()
                first = False
                async for msg in self.pubsub.listen():
                    if msg["type"] in ("message", "pmessage"):
                        self.hub.publish(msg["channel"], json.loads(msg["data"]))
            except (RedisConnectionError, OSError) as e:
                log.warning("pub/sub connection lost (%s); reconnecting, clients will resnapshot", e)
                await asyncio.sleep(0.5)


def build_deps(settings: Settings) -> Deps:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    engine = make_engine(settings, pool_size=10)
    hub = Hub()
    matcher = RedisMatcherClient(redis, settings.request_timeout_s)
    subs = Subscriptions(redis, hub)
    queue = ArqQueue(settings.redis_url)

    async def health() -> dict[str, str]:
        out = {"matcher": "down", "redis": "down", "database": "down"}
        try:
            await redis.ping()
            out["redis"] = "up"
            if await redis.exists(keys.HEARTBEAT):
                out["matcher"] = "up"
        except Exception:  # noqa: BLE001 - reported, not raised
            pass
        try:
            async with engine.connect() as conn:
                await conn.execute(select(1))
            out["database"] = "up"
        except Exception:  # noqa: BLE001
            pass
        return out

    async def catalog() -> dict[str, Any] | None:
        raw = await redis.get(keys.CATALOG)
        return json.loads(raw) if raw else None

    async def start() -> None:
        await matcher.start()
        await subs.start()
        await queue.start()

    async def stop() -> None:
        await matcher.stop()
        await subs.stop()
        await queue.stop()
        await redis.aclose()
        await engine.dispose()

    return Deps(matcher=matcher, store=PgStore(engine), cache=RedisBookCache(redis),
                limiter=RedisRateLimiter(redis, settings.rate_limit_per_s), hub=hub, queue=queue, health=health,
                counter=RedisCounter(redis), catalog=catalog, start=start, stop=stop,
                extra={"worker_id": matcher.worker_id})
