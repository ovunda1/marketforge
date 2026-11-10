"""The matcher (M10): the only process that owns the engines, the live sequencers and the
journals. Gateway workers forward requests to it; nothing else writes a journal.

One `Engine`, journal, intake log and market-data feed per symbol. `process_batch`
handles a batch of requests in four phases, so a batch is one group commit:

0. Route and check each request: deadline, symbol, ownership, idempotency key. Assign
   order ids (global) and seq_nos (per symbol). Errors here are never journaled.
1. Write every intake entry of the batch, then fsync each touched intake log.
2. For each command in order: append its journal record, then run it through the engine.
3. fsync each touched journal. Only then is the result returned, so no reply, execution
   report or market data can leave before the batch is durable.

Intake before journal (both written and synced) means a journal record on disk always
has its intake entry, even after an OS crash in which the kernel had already written
some journal pages back on its own: a retried client_order_id is then recognised
instead of creating a second order. docs/learning/m10.md, "Durability".

Restart (`Matcher.open`): per symbol, `JournalWriter.resume` (trims a torn final record),
drop intake entries beyond the journal (orphans of a crash between phases 1 and 2), load
the newest usable snapshot and replay the journal tail.

Bounded state (after M11b): the order registry holds live orders only (an order leaves it
when the engine no longer has it, so a cancel of a filled order is NOT_FOUND) and is
rebuilt on restart from the engine itself. Idempotency keys live for a window of journal
time (`key_window_ns`, 24 h by default): each snapshot seals the active intake log into a
segment, a segment's keys expire once its last timestamp falls out of the window, and
restart reads only the active log and the segments inside the window. So startup time
and memory follow the last day of traffic, not the whole history.
"""

from __future__ import annotations

import fcntl
import os
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mfengine as mf

from services.common.events import command_from_request, event_to_dict
from services.common.instruments import Instrument
from services.common.protocol import ADMIN_OPS, OPS, Reply, Request
from services.matcher.intake import (IntakeEntry, IntakeLog, Segment, drop_segments_after, segments,
                                     split_into_segments, trim_sealed_prefix)
from services.matcher.marketdata import BookFeed

KEEP_SNAPSHOTS = 3
KEY_WINDOW_NS = 24 * 3600 * 10**9
_KeyId = tuple[int, str, str]  # (account, "new"|"mod", key)


class MatcherLocked(RuntimeError):
    """Another matcher holds this data directory: there is exactly one writer."""


class IntakeMismatch(RuntimeError):
    """A journal record has no intake entry. The write order makes this impossible, so
    it means the files were edited or lost: an operator has to look."""


@dataclass
class Recovery:
    snapshot_seq_no: int = 0
    replayed: int = 0
    trimmed_bytes: int = 0
    orphans: int = 0


@dataclass
class BatchResult:
    replies: list[Reply]  # replies[i] answers requests[i]
    market_data: list[dict[str, Any]] = field(default_factory=list)
    books: dict[str, dict[str, Any]] = field(default_factory=dict)  # snapshots after this batch
    executions: list[tuple[int, dict[str, Any]]] = field(default_factory=list)  # (account, report)


@dataclass(frozen=True)
class _Key:
    symbol: str
    order_id: int | None
    seq_no: int
    payload_hash: str


@dataclass
class _Plan:
    index: int
    symbol: str
    command: Any
    entry: IntakeEntry
    request: Request
    keys: list[_KeyId]  # registered by this plan (undone if it is not journaled)


class _Book:
    """One symbol: engine, journal, intake log, feed."""

    def __init__(self, instrument: Instrument, root: Path, depth: int) -> None:
        self.instrument = instrument
        self.symbol = instrument.symbol
        self.dir = root / instrument.symbol
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "snapshots").mkdir(exist_ok=True)
        self.journal_path = self.dir / "journal.mfj"
        self.engine = mf.Engine()
        self.writer: Any = None
        self.intake: IntakeLog | None = None
        self.feed = BookFeed(instrument.symbol, depth)
        self.halted: str | None = None
        self.next_seq = 1
        self.last_ts: int | None = None
        self.journal_durable = 0
        self.since_snapshot = 0
        self.active_keys: list[tuple[_KeyId, int]] = []  # (key, seq_no) of entries in the active intake log
        self.sealed_keys: deque[tuple[int, list[tuple[_KeyId, int]]]] = deque()  # (segment last_ts, keys), oldest first

    def timestamp(self, now: int) -> int:
        ts = now if self.last_ts is None else max(now, self.last_ts)
        self.last_ts = ts
        return ts


class Matcher:
    def __init__(self, data_dir: Path, clock: Callable[[], int], snapshot_every: int, fsync: bool,
                 key_window_ns: int = KEY_WINDOW_NS) -> None:
        self.data_dir = data_dir
        self._clock = clock
        self._snapshot_every = snapshot_every
        self._fsync = fsync
        self._key_window = key_window_ns
        self._books: dict[str, _Book] = {}
        self._orders: dict[int, tuple[str, int]] = {}  # live order_id -> (symbol, account)
        self._keys: dict[_KeyId, _Key] = {}
        self._next_order_id = 1
        self._lock_fd = -1
        self._epoch = 0
        self.recovery: dict[str, Recovery] = {}
        self.trace: list[str] | None = None  # tests: the order of durable operations
        # Tests: called after "intake.write", "journal.append", "engine" and before
        # "intake.sync", "journal.sync", to simulate a crash at that point.
        self.fault_hook: Callable[[str], None] | None = None

    # --- Opening and recovery -------------------------------------------------------------------

    @classmethod
    def open(cls, data_dir: str | Path, instruments: Sequence[Instrument], *, clock: Callable[[], int],
             snapshot_every: int = 10_000, fsync: bool = True, depth: int = 50,
             key_window_ns: int = KEY_WINDOW_NS) -> Matcher:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        m = cls(data_dir, clock, snapshot_every, fsync, key_window_ns)
        m._lock()
        try:
            for instrument in instruments:
                book = _Book(instrument, data_dir, depth)
                m._books[instrument.symbol] = book
                m.recovery[instrument.symbol] = m._recover(book)
            for book in m._books.values():
                if not book.halted:
                    book.feed.reset(book.engine, m._epoch)
        except BaseException:
            m.abandon()
            raise
        return m

    def _lock(self) -> None:
        self._lock_fd = os.open(self.data_dir / "LOCK", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = -1
            raise MatcherLocked(f"{self.data_dir} is in use by another matcher") from None

    def _recover(self, book: _Book) -> Recovery:
        info = Recovery()
        if not book.journal_path.exists():
            tmp = book.journal_path.with_suffix(".new")
            tmp.unlink(missing_ok=True)
            mf.JournalWriter(tmp).close()  # header only, then an atomic rename
            os.replace(tmp, book.journal_path)
        book.writer = mf.JournalWriter.resume(book.journal_path)
        info.trimmed_bytes = book.writer.trimmed_bytes
        last = book.writer.last_seq_no
        book.journal_durable = book.writer.bytes_written

        active = book.dir / "intake.jsonl"
        sealed = segments(book.dir)
        trim_sealed_prefix(active, sealed[-1].last_seq + 1 if sealed else 1, fsync=self._fsync)
        if not sealed and last > self._snapshot_every:
            self._split_legacy(book, last)
            sealed = segments(book.dir)
        book.intake = IntakeLog.open(active, first=sealed[-1].last_seq + 1 if sealed else 1)
        entries = book.intake.entries
        end = book.intake.first - 1 + len(entries)
        if end > last:
            info.orphans = end - max(last, book.intake.first - 1)
            book.intake.truncate_after(last)
            entries = [e for e in entries if e.seq_no <= last]
        if sealed and sealed[-1].last_seq > last:  # only without fsync: the journal lost synced-looking records
            info.orphans += drop_segments_after(book.dir, last, fsync=self._fsync)
            sealed = segments(book.dir)
            book.intake.first = last + 1  # truncate_after(last) above emptied it
            entries = []
        if (sealed[-1].last_seq if sealed else 0) + len(entries) < last:
            raise IntakeMismatch(f"{book.symbol}: journal has {last} records, intake log ends at "
                                 f"{(sealed[-1].last_seq if sealed else 0) + len(entries)}")
        book.intake.durable_size = book.intake.size

        reader = self._restore(book, last, info)
        if book.halted is None:
            try:
                for rec in reader:
                    book.engine.process(rec.command, rec.timestamp)
                    info.replayed += 1
            except mf.MarketForgeError as e:
                book.halted = f"engine failed during recovery: {e}"
        book.next_seq = last + 1
        book.last_ts = book.engine.last_timestamp if not book.halted else None
        self._load_registry(book)
        self._load_keys(book, sealed, entries)

        if last == 0 and book.instrument.risk_config is not None:
            self._journal_config(book)
        return info

    def _split_legacy(self, book: _Book, last: int) -> None:
        """A data directory from before segments has one intake log from seq_no 1. Split it
        once into segments of `snapshot_every` entries, each named with its last record's
        journal timestamp, so the key window applies to it like to any other."""
        bounds = set(range(self._snapshot_every, last + 1, self._snapshot_every))
        if not bounds:
            return
        ends = []
        for rec in mf.JournalReader(book.journal_path):
            if rec.seq_no in bounds:
                ends.append((rec.seq_no, rec.timestamp))
                if len(ends) == len(bounds):
                    break
        split_into_segments(book.dir, ends, fsync=self._fsync)

    def _restore(self, book: _Book, last: int, info: Recovery) -> Any:
        """The newest snapshot that loads and is not beyond the journal, and a reader
        positioned after it; a fresh engine and a reader at the start otherwise."""
        for path in sorted((book.dir / "snapshots").glob("snap-*.mfsnap"), reverse=True):
            try:
                snap = mf.read_snapshot_file(path)
                seq = snap.last_seq_no or 0
                if seq == 0 or seq > last:
                    continue
                engine = mf.Engine.from_snapshot(snap)
                reader = mf.JournalReader(book.journal_path)
                reader.seek(snap.journal_offset, seq, snap.last_timestamp)
            except (mf.MarketForgeError, OSError, ValueError):
                continue
            book.engine = engine
            info.snapshot_seq_no = seq
            return reader
        return mf.JournalReader(book.journal_path)

    def _load_registry(self, book: _Book) -> None:
        """The live orders, from the engine (which knows every resting order's owner)."""
        if book.halted:
            return
        for side in (mf.Side.BUY, mf.Side.SELL):
            for price, _, _ in book.engine.depth(side):
                for oid in book.engine.level_orders(side, price):
                    view = book.engine.order(oid)
                    assert view is not None
                    self._orders[oid] = (book.symbol, view.account_id)
                    self._next_order_id = max(self._next_order_id, oid + 1)

    def _load_keys(self, book: _Book, sealed: list[Segment], active: list[IntakeEntry]) -> None:
        """Keys from the active log and the segments inside the window, newest first; older
        segments are opened only while no order id has been seen yet (to continue the
        global order ids after a long idle spell)."""
        now = book.last_ts if book.last_ts is not None else (sealed[-1].last_ts if sealed else None)
        cutoff = None if now is None else now - self._key_window
        found_id = self._scan_ids(active)
        book.active_keys = [(k, e.seq_no) for e in active if (k := self._register_key(book.symbol, e)) is not None]
        window: list[tuple[int, list[tuple[_KeyId, int]]]] = []
        for seg in reversed(sealed):
            inside = cutoff is None or seg.last_ts >= cutoff
            if not inside and found_id:
                break
            seg_entries = seg.load()
            found_id = self._scan_ids(seg_entries) or found_id
            if inside:
                window.append((seg.last_ts, [(k, e.seq_no) for e in seg_entries
                                             if (k := self._register_key(book.symbol, e, replace=False))]))
        book.sealed_keys = deque(reversed(window))

    def _scan_ids(self, entries: list[IntakeEntry]) -> bool:
        ids = [e.order_id for e in entries if e.op == "new" and e.order_id is not None]
        if ids:
            self._next_order_id = max(self._next_order_id, max(ids) + 1)
        return bool(ids)

    def _register_key(self, symbol: str, e: IntakeEntry, replace: bool = True) -> _KeyId | None:
        """Registers an entry's idempotency key. `replace=False` keeps a newer entry's key
        (segments are read newest first)."""
        if e.key is None or e.op not in ("new", "cancel", "replace"):
            return None
        key = (e.account_id, "new" if e.op == "new" else "mod", e.key)
        if replace or key not in self._keys:
            self._keys[key] = _Key(symbol, e.order_id, e.seq_no, e.payload_hash)
        return key

    def _expire_keys(self, book: _Book) -> None:
        if book.last_ts is None:
            return
        cutoff = book.last_ts - self._key_window
        while book.sealed_keys and book.sealed_keys[0][0] < cutoff:
            _, keys = book.sealed_keys.popleft()
            for key, seq in keys:
                known = self._keys.get(key)
                if known is not None and (known.symbol, known.seq_no) == (book.symbol, seq):
                    del self._keys[key]  # still this entry's key, not a newer use of an expired one

    def _journal_config(self, book: _Book) -> None:
        assert book.intake is not None and book.instrument.risk_config is not None
        commands = mf.load_risk_config(book.instrument.risk_config)
        entries = [IntakeEntry(seq_no=book.next_seq + i, request_id="config", op="config", account_id=0, key=None,
                               payload_hash="", order_id=None) for i in range(len(commands))]
        book.intake.append(entries)
        book.intake.sync()
        ts = book.timestamp(self._clock())
        for cmd, entry in zip(commands, entries):
            cmd = cmd.with_seq_no(entry.seq_no)
            book.writer.append(mf.JournalRecord(timestamp=ts, command=cmd))
            book.engine.process(cmd, ts)
        book.writer.sync()
        book.journal_durable = book.writer.bytes_written
        book.next_seq += len(commands)

    # --- Introspection -------------------------------------------------------------------------

    @property
    def symbols(self) -> list[str]:
        return list(self._books)

    @property
    def epoch(self) -> int:
        return self._epoch

    @epoch.setter
    def epoch(self, value: int) -> None:
        """A new epoch (each matcher start): market data restarts at md_seq 0."""
        self._epoch = value
        for book in self._books.values():
            if not book.halted:
                book.feed.reset(book.engine, value)

    def engine(self, symbol: str) -> mf.Engine:
        return self._books[symbol].engine

    def halted(self, symbol: str) -> bool:
        return self._books[symbol].halted is not None

    def last_seq_no(self, symbol: str) -> int:
        return int(self._books[symbol].writer.last_seq_no)

    def book_snapshot(self, symbol: str) -> dict[str, Any]:
        return self._books[symbol].feed.snapshot()

    def durable_sizes(self) -> dict[Path, int]:
        """Bytes of each journal and intake log known to be on stable storage."""
        out: dict[Path, int] = {}
        for b in self._books.values():
            assert b.intake is not None
            out[b.journal_path] = b.journal_durable
            out[b.intake.path] = b.intake.durable_size
        return out

    # --- Processing ----------------------------------------------------------------------------

    def _mark(self, event: str, before: bool = False) -> None:
        if before and self.fault_hook:
            self.fault_hook(event)
        if self.trace is not None:
            self.trace.append(event)
        if not before and self.fault_hook:
            self.fault_hook(event)

    def _error(self, req: Request, code: str, message: str) -> Reply:
        return Reply(request_id=req.request_id, status="error", code=code, message=message)

    def _dedupe(self, req: Request, key: tuple[int, str, str] | None) -> Reply | None:
        if key is None or key not in self._keys:
            return None
        known = self._keys[key]
        if known.payload_hash != req.payload_hash():
            return self._error(req, "IDEMPOTENCY_CONFLICT", f"key {req.key!r} was used for a different request")
        return Reply(request_id=req.request_id, status="ok", symbol=known.symbol, order_id=known.order_id,
                     seq_no=known.seq_no, replayed=True)

    def _plan(self, i: int, req: Request, plans: list[_Plan]) -> Reply | None:
        """Phase 0 for one request: an error or replay reply, or plans appended."""
        if req.op not in OPS:
            return self._error(req, "BAD_REQUEST", f"unknown op {req.op!r}")
        if req.op in ADMIN_OPS:
            targets = [req.symbol] if req.symbol is not None else list(self._books)
            if any(s not in self._books for s in targets):
                return self._error(req, "UNKNOWN_SYMBOL", f"unknown symbol {req.symbol!r}")
            if any(self._books[s].halted for s in targets):
                return self._error(req, "SYMBOL_HALTED", "a target symbol is halted after an engine failure")
            try:
                command = command_from_request(req, order_id=0)
            except (KeyError, ValueError, TypeError) as e:
                return self._error(req, "BAD_REQUEST", str(e))
            for s in targets:
                book = self._books[s]
                entry = IntakeEntry(seq_no=book.next_seq, request_id=req.request_id, op=req.op,
                                    account_id=req.account_id, key=None, payload_hash=req.payload_hash(),
                                    order_id=None)
                book.next_seq += 1
                plans.append(_Plan(i, s, command, entry, req, []))
            return None

        if req.op == "new":
            symbol = req.symbol
            if symbol not in self._books:
                return self._error(req, "UNKNOWN_SYMBOL", f"unknown symbol {symbol!r}")
            key = (req.account_id, "new", req.key) if req.key is not None else None
            order_id = self._next_order_id
        else:
            key = (req.account_id, "mod", req.key) if req.key is not None else None
            # A retry first: the order it cancelled or filled has left the registry.
            replay = self._dedupe(req, key)
            if replay is not None:
                return replay
            known = self._orders.get(req.order_id) if req.order_id is not None else None
            if known is None or known[1] != req.account_id:
                return self._error(req, "NOT_FOUND", f"no order {req.order_id} for this account")
            symbol = known[0]
            order_id = req.order_id  # type: ignore[assignment]
        book = self._books[symbol]
        if book.halted:
            return self._error(req, "SYMBOL_HALTED", f"{symbol} is halted after an engine failure")
        replay = self._dedupe(req, key)
        if replay is not None:
            return replay
        try:
            command = command_from_request(req, order_id=order_id)
        except (KeyError, ValueError, TypeError) as e:
            return self._error(req, "BAD_REQUEST", str(e))
        entry = IntakeEntry(seq_no=book.next_seq, request_id=req.request_id, op=req.op, account_id=req.account_id,
                            key=req.key, payload_hash=req.payload_hash(), order_id=order_id)
        book.next_seq += 1
        if req.op == "new":
            self._next_order_id += 1
            self._orders[order_id] = (symbol, req.account_id)
        keys = []
        if key is not None:
            self._keys[key] = _Key(symbol, order_id, entry.seq_no, entry.payload_hash)
            keys.append(key)
        plans.append(_Plan(i, symbol, command, entry, req, keys))
        return None

    def _account_of(self, ev: dict[str, Any], req: Request) -> int | None:
        if ev["type"] == "fill":
            return int(ev["account_id"])
        if ev["type"] in ("ack", "reject"):
            return req.account_id
        if ev["type"] in ("cancelled", "replaced"):
            known = self._orders.get(ev["order_id"])
            return known[1] if known else None
        return None

    def process_batch(self, requests: Sequence[Request]) -> BatchResult:
        now = self._clock()
        replies: list[Reply | None] = [None] * len(requests)
        plans: list[_Plan] = []
        for i, req in enumerate(requests):
            if req.deadline_ns < now:
                replies[i] = self._error(req, "EXPIRED", "the deadline passed before the matcher saw the request")
            else:
                replies[i] = self._plan(i, req, plans)
        touched = list(dict.fromkeys(p.symbol for p in plans))

        # Phase 1: intake entries, written and synced before any journal write.
        for s in touched:
            book = self._books[s]
            assert book.intake is not None
            book.intake.append([p.entry for p in plans if p.symbol == s])
            self._mark(f"intake.write:{s}")
        for s in touched:
            self._mark(f"intake.sync:{s}", before=True)
            if self._fsync:
                self._books[s].intake.sync()  # type: ignore[union-attr]

        # Phase 2: journal record, then the engine, command by command.
        results: dict[int, list[tuple[str, int, list[dict[str, Any]], Reply | None]]] = {}
        trades: dict[str, list[dict[str, Any]]] = {s: [] for s in touched}
        executions: list[tuple[int, dict[str, Any]]] = []
        touched_orders: dict[str, set[int]] = {s: set() for s in touched}  # may have left the book
        for p in plans:
            book = self._books[p.symbol]
            if book.halted:  # failed earlier in this batch: not journaled, its intake entry is an orphan
                for key in p.keys:
                    self._keys.pop(key, None)
                if p.request.op == "new":
                    self._orders.pop(p.entry.order_id, None)  # type: ignore[arg-type]
                results.setdefault(p.index, []).append(
                    (p.symbol, p.entry.seq_no, [], self._error(p.request, "SYMBOL_HALTED",
                                                               f"{p.symbol} is halted after an engine failure")))
                continue
            command = p.command.with_seq_no(p.entry.seq_no)
            ts = book.timestamp(now)
            book.writer.append(mf.JournalRecord(timestamp=ts, command=command))
            self._mark(f"journal.append:{p.symbol}:{p.entry.seq_no}")
            book.active_keys.extend((key, p.entry.seq_no) for key in p.keys)
            if p.entry.order_id is not None:
                touched_orders[p.symbol].add(p.entry.order_id)
            try:
                events = [event_to_dict(e) for e in book.engine.process(command, ts)]
            except mf.MarketForgeError as e:
                book.halted = f"engine failed at seq_no {p.entry.seq_no}: {e}"
                results.setdefault(p.index, []).append(
                    (p.symbol, p.entry.seq_no, [], self._error(p.request, "ENGINE_FAILED", book.halted)))
                continue
            self._mark(f"engine:{p.symbol}:{p.entry.seq_no}")
            book.since_snapshot += 1
            for ev in events:
                if ev["type"] == "trade":
                    trades[p.symbol].append(ev)
                elif "order_id" in ev:
                    touched_orders[p.symbol].add(ev["order_id"])
                account = self._account_of(ev, p.request)
                if account is not None:
                    executions.append((account, {"type": "execution", "symbol": p.symbol, "seq_no": ev["seq_no"],
                                                 "account_id": account, "event": ev}))
            results.setdefault(p.index, []).append((p.symbol, p.entry.seq_no, events, None))

        # Phase 3: the journals. Nothing is returned before this.
        for s in touched:
            book = self._books[s]
            self._mark(f"journal.sync:{s}", before=True)
            if self._fsync:
                book.writer.sync()
                book.journal_durable = book.writer.bytes_written

        for index, parts in results.items():
            replies[index] = self._reply(requests[index], parts)
        out = BatchResult(replies=[r for r in replies if r is not None], executions=executions)
        assert len(out.replies) == len(requests)
        for s in touched:
            book = self._books[s]
            if book.halted:
                continue
            update = book.feed.after_batch(book.engine, trades[s])
            if update is not None:
                out.market_data.append(update)
                out.books[s] = book.feed.snapshot()
            for oid in touched_orders[s]:
                if book.engine.order(oid) is None:  # filled, cancelled, rejected or expired
                    self._orders.pop(oid, None)
            if book.since_snapshot >= self._snapshot_every:
                self._write_snapshot(book)
        return out

    def _reply(self, req: Request, parts: list[tuple[str, int, list[dict[str, Any]], Reply | None]]) -> Reply:
        if req.op in ADMIN_OPS:
            failed = [r for *_, r in parts if r is not None]
            if failed:
                return failed[0]
            rejects = [e["reason"] for _, _, evs, _ in parts for e in evs if e["type"] == "admin_reject"]
            return Reply(request_id=req.request_id, status="rejected" if rejects else "ok",
                         reason=rejects[0] if rejects else None,
                         results=[{"symbol": s, "seq_no": seq, "events": evs} for s, seq, evs, _ in parts])
        [(symbol, seq, events, error)] = parts
        if error is not None:
            return error
        reject = next((e for e in events if e["type"] == "reject"), None)
        order_id = req.order_id if req.op != "new" else next(
            (e["order_id"] for e in events if e["type"] in ("ack", "reject")), None)
        return Reply(request_id=req.request_id, status="rejected" if reject else "ok", symbol=symbol,
                     order_id=order_id, seq_no=seq, events=events, reason=reject["reason"] if reject else None)

    # --- Snapshots and shutdown ----------------------------------------------------------------

    def _write_snapshot(self, book: _Book) -> None:
        seq = book.engine.last_seq_no
        if not seq:
            return
        snap = book.engine.snapshot(last_timestamp=book.engine.last_timestamp, journal_offset=book.writer.bytes_written)
        path = book.dir / "snapshots" / f"snap-{seq:012d}.mfsnap"
        tmp = path.with_suffix(".tmp")
        mf.write_snapshot_file(tmp, snap)
        os.replace(tmp, path)
        book.since_snapshot = 0
        for old in sorted((book.dir / "snapshots").glob("snap-*.mfsnap"))[:-KEEP_SNAPSHOTS]:
            old.unlink()
        # Seal the intake entries the snapshot covers; restart no longer needs to parse them.
        assert book.intake is not None
        ts = book.engine.last_timestamp
        if ts is not None and book.next_seq - 1 == seq >= book.intake.first:
            book.intake.rotate(seq, ts, fsync=self._fsync)
            book.sealed_keys.append((ts, book.active_keys))
            book.active_keys = []
            self._expire_keys(book)

    def snapshot_all(self) -> None:
        for book in self._books.values():
            if not book.halted and book.since_snapshot:
                self._write_snapshot(book)

    def close(self) -> None:
        """Clean shutdown: sync, snapshot every symbol, release the lock."""
        for book in self._books.values():
            if book.writer is not None and self._fsync:
                book.writer.sync()
        self.snapshot_all()
        self.abandon()

    def abandon(self) -> None:
        """Drop every file and the lock without syncing or snapshotting: what a killed
        process leaves behind (tests use it to simulate a crash)."""
        for book in self._books.values():
            if book.writer is not None:
                book.writer.close()
            if book.intake is not None:
                book.intake.close()
        if self._lock_fd >= 0:
            os.close(self._lock_fd)
            self._lock_fd = -1
