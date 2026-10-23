"""The projector process (M10): tails every symbol's journal and keeps the Postgres
projection up to date, asynchronously and off the matching path.

    python -m services.projector run      # the compose service
    python -m services.projector verify   # Postgres == a fresh replay of every journal (JSON report)
    python -m services.projector rebuild  # forget the projection; `run` rebuilds it from the journals
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

import mfengine as mf

from services.common.config import Settings
from services.common.instruments import load_instruments
from services.db.engine import make_engine
from services.projector.pg import PgStore
from services.projector.projection import CursorMismatch, MemoryStore, Projection
from services.projector.source import SymbolSource

log = logging.getLogger("mf.projector")
BATCH = 1000
IDLE_S = 0.02


def _symbols(settings: Settings) -> list[str]:
    assert settings.instruments_file is not None
    return [i.symbol for i in load_instruments(settings.instruments_file)]


async def _start(store: PgStore, settings: Settings, symbol: str) -> tuple[SymbolSource, int]:
    source = SymbolSource(settings.data_dir, symbol)
    cursor = await store.cursor(symbol)
    if source.needs_rebuild(cursor):
        log.warning("%s: projection cursor %d is ahead of the journal; rebuilding", symbol, cursor)
        await store.reset(symbol)
        cursor = 0
    source.start(cursor)
    return source, cursor


async def run(settings: Settings) -> None:
    engine = make_engine(settings)
    store = PgStore(engine)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    sources: dict[str, tuple[SymbolSource, int]] = {}
    while not stop.is_set():
        busy = False
        for symbol in _symbols(settings):
            if symbol not in sources:
                if not (settings.data_dir / symbol / "journal.mfj").exists():
                    continue  # the matcher has not created it yet
                sources[symbol] = await _start(store, settings, symbol)
            source, cursor = sources[symbol]
            batch = source.poll(BATCH)
            if not batch:
                if source.failed:
                    log.error("%s: %s; not projecting further", symbol, source.failed)
                continue
            busy = True
            try:
                await store.apply(symbol, Projection.ops(symbol, batch), expected=cursor, to=batch[-1].seq_no)
            except CursorMismatch as e:
                log.warning("%s; restarting this symbol from the stored cursor", e)
                del sources[symbol]
                continue
            sources[symbol] = (source, batch[-1].seq_no)
        if not busy:
            try:
                await asyncio.wait_for(stop.wait(), IDLE_S)
            except TimeoutError:
                pass
    await engine.dispose()


async def verify(settings: Settings) -> dict[str, Any]:
    """Postgres against a fresh in-memory projection of every journal, at the cursors
    Postgres reports (one consistent read). `ok` also requires every cursor to have
    reached the end of its journal."""
    engine = make_engine(settings)
    stored = await PgStore(engine).dump()
    await engine.dispose()
    fresh = MemoryStore()
    report: dict[str, Any] = {"symbols": {}}
    caught_up = True
    for symbol in _symbols(settings):
        source = SymbolSource(settings.data_dir, symbol)
        cursor = stored["cursors"].get(symbol, 0)
        source.start(0)
        records = source.poll(cursor) if cursor else []
        if records:
            fresh.apply(symbol, Projection.ops(symbol, records), expected=0, to=records[-1].seq_no)
        rest = source.poll(10**12)
        last = cursor + len(rest)
        caught_up &= not rest
        report["symbols"][symbol] = {
            "cursor": cursor, "journal_last": last, "gapless": True,  # the reader refuses gaps
            "orders": sum(1 for r in fresh.tables["orders"].values() if r["symbol"] == symbol),
            "trades": sum(1 for k in fresh.tables["trades"] if k[0] == symbol),
            "book": [[list(x) for x in source.engine.depth(mf.Side.BUY, settings.depth)],
                     [list(x) for x in source.engine.depth(mf.Side.SELL, settings.depth)]],
        }
    expected = fresh.dump()
    diffs = [name for name in expected if expected[name] != stored.get(name)]
    report["mismatched_tables"] = diffs
    report["caught_up"] = caught_up
    report["ok"] = not diffs and caught_up
    return report


async def rebuild(settings: Settings) -> None:
    engine = make_engine(settings)
    store = PgStore(engine)
    for symbol in _symbols(settings):
        await store.reset(symbol)
    await engine.dispose()
