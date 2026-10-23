"""The matcher process (M10): Redis in, Redis out, `Matcher` in the middle.

    gateway workers --XADD mf:cmd--> matcher --XADD mf:reply:<worker>--> gateway workers
                                             --PUBLISH md:<sym>, exec:<acct>; SET mf:book:<sym>

Requests are read with XREADGROUP ... NOACK, so each is delivered at most once: a
request read but not yet journaled when the process dies is lost, the gateway times out
(504, "outcome unknown") and the client retries with the same client_order_id, which
the matcher recognises if the first attempt was journaled. Requests still waiting in
the stream when the matcher restarts are processed if their deadline has not passed.

After each batch (made durable by `Matcher.process_batch`) the replies, execution
reports and market data go out in one MULTI/EXEC, so a book snapshot in `mf:book:<sym>`
and the update that produced it are published together. The matcher never waits for a
WebSocket client: those hang off the gateway workers' Redis subscriptions, and Redis
disconnects a subscriber that falls too far behind (client-output-buffer-limit pubsub).

The wall clock lives here, in the services layer: `time.time_ns()`, made non-decreasing
per symbol by the matcher. The engine itself still never reads a clock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from typing import Any

from prometheus_client import Counter, Histogram, start_http_server
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from services.common import keys
from services.common.config import Settings
from services.common.instruments import load_instruments
from services.common.protocol import Request
from services.matcher.core import BatchResult, Matcher

log = logging.getLogger("mf.matcher")
BATCH = 512
CONSUMER = "matcher"
REPLY_TTL_S = 60

COMMANDS = Counter("mf_matcher_commands_total", "Requests processed", ["op", "status"])
REJECTS = Counter("mf_matcher_rejects_total", "Engine rejects", ["reason"])
BATCH_SIZE = Histogram("mf_matcher_batch_size", "Requests per batch", buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256, 512))
BATCH_SECONDS = Histogram("mf_matcher_batch_seconds", "Time to make a batch durable (journal + fsync)")


async def publish(redis: Redis, requests: list[Request], result: BatchResult) -> None:
    pipe = redis.pipeline(transaction=True)
    for req, reply in zip(requests, result.replies):
        pipe.xadd(req.reply_to, {"m": reply.to_json()}, maxlen=10_000, approximate=True)
        pipe.expire(req.reply_to, REPLY_TTL_S)
    for symbol, snap in result.books.items():
        pipe.set(keys.book(symbol), json.dumps(snap))
    for update in result.market_data:
        pipe.publish(keys.md_channel(update["symbol"]), json.dumps(update))
    for account, report in result.executions:
        pipe.publish(keys.exec_channel(account), json.dumps(report))
    await pipe.execute()


async def publish_snapshots(redis: Redis, matcher: Matcher) -> None:
    """At start (a new epoch): every client sees the new epoch and resnapshots."""
    pipe = redis.pipeline(transaction=True)
    for symbol in matcher.symbols:
        if matcher.halted(symbol):
            continue
        snap = matcher.book_snapshot(symbol)
        pipe.set(keys.book(symbol), json.dumps(snap))
        pipe.publish(keys.md_channel(symbol), json.dumps(snap))
    await pipe.execute()


async def heartbeat(redis: Redis, epoch: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await redis.set(keys.HEARTBEAT, epoch, ex=3)
        try:
            await asyncio.wait_for(stop.wait(), 1.0)
        except TimeoutError:
            pass
    await redis.delete(keys.HEARTBEAT)


def _parse(entries: list[Any]) -> list[Request]:
    out = []
    for _, fields in entries:
        try:
            out.append(Request.from_json(fields["m"]))
        except (KeyError, TypeError, ValueError) as e:
            log.error("dropping a malformed request: %s", e)
    return out


def _count(requests: list[Request], result: BatchResult) -> None:
    for req, reply in zip(requests, result.replies):
        COMMANDS.labels(req.op, reply.status if reply.status != "error" else reply.code or "error").inc()
        if reply.status == "rejected" and reply.reason:
            REJECTS.labels(reply.reason).inc()


async def serve(settings: Settings) -> None:
    assert settings.instruments_file is not None
    instruments = load_instruments(settings.instruments_file)
    matcher = Matcher.open(settings.data_dir, instruments, clock=time.time_ns, snapshot_every=settings.snapshot_every,
                           fsync=settings.fsync, depth=settings.depth)
    for symbol, info in matcher.recovery.items():
        log.info("%s: seq_no %d, snapshot %d, replayed %d, trimmed %d bytes, %d orphan intake entries", symbol,
                 matcher.last_seq_no(symbol), info.snapshot_seq_no, info.replayed, info.trimmed_bytes, info.orphans)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    matcher.epoch = int(await redis.incr(keys.EPOCH))
    try:
        await redis.xgroup_create(keys.COMMANDS, keys.COMMANDS_GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    await publish_snapshots(redis, matcher)
    log.info("matcher up, epoch %d, symbols %s", matcher.epoch, ",".join(matcher.symbols))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    beat = asyncio.create_task(heartbeat(redis, matcher.epoch, stop))
    try:
        while not stop.is_set():
            resp = await redis.xreadgroup(keys.COMMANDS_GROUP, CONSUMER, {keys.COMMANDS: ">"}, count=BATCH, block=100,
                                          noack=True)
            if not resp:
                continue
            requests = _parse(resp[0][1])
            if not requests:
                continue
            BATCH_SIZE.observe(len(requests))
            with BATCH_SECONDS.time():
                result = matcher.process_batch(requests)
            await publish(redis, requests, result)
            _count(requests, result)
    finally:
        stop.set()
        await beat
        matcher.close()
        await redis.aclose()
        log.info("matcher stopped cleanly")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    start_http_server(9101)
    asyncio.run(serve(Settings.from_env()))
