"""The intake log (M10): one JSON line per sequenced command, per symbol, next to the
journal. It holds what the journal's binary format has no field for: the request id,
the client_order_id or idempotency key with its payload hash, and the order id the
matcher assigned. The matcher rebuilds its idempotency map and order registry from it
on restart, and the projector reads client_order_ids from it.

Write order (docs/learning/m10.md, "Durability"): a batch's intake entries are written
and fsynced before the first of its journal records is written. So any journal record
that reaches the disk, even one the kernel wrote back on its own before an OS crash,
has a durable intake entry. Entries beyond the journal's last seq_no are orphans (the
crash came between the two writes): they never happened, and restart removes them.

Segments (after M11b): each time the matcher writes a snapshot it seals the active log
into `intake/seg-<last seq_no>-<last timestamp>.jsonl` and starts an empty one, so the
active `intake.jsonl` only holds entries after the newest snapshot. Restart parses the
active log (for orphans) and only the sealed segments still inside the idempotency
window (for keys); older segments stay on disk for the projector and are never read
again by the matcher.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

SEGMENT_DIR = "intake"
_SEGMENT = re.compile(r"^seg-(\d{12})-(-?\d+)\.jsonl$")


class IntakeError(RuntimeError):
    """The intake log is damaged somewhere other than a torn final line."""


@dataclass(frozen=True)
class IntakeEntry:
    seq_no: int
    request_id: str
    op: str  # a request op, or "config" for the risk config journaled at first start
    account_id: int
    key: str | None
    payload_hash: str
    order_id: int | None  # the new order's id, or the target of a cancel or replace

    def line(self) -> bytes:
        return (json.dumps(asdict(self), sort_keys=True) + "\n").encode()


def _parse(path: Path, data: bytes, first: int = 1) -> tuple[list[IntakeEntry], int]:
    """Entries and the byte length of the complete lines (a torn last line is left out).
    The first entry must have seq_no `first`, and each next one the next seq_no."""
    entries: list[IntakeEntry] = []
    good = data.rfind(b"\n") + 1
    for n, line in enumerate(data[:good].splitlines(), 1):
        try:
            entry = IntakeEntry(**json.loads(line))
        except (ValueError, TypeError) as e:
            raise IntakeError(f"{path}: line {n} is damaged: {e}") from None
        expected = entries[-1].seq_no + 1 if entries else first
        if entry.seq_no != expected:
            raise IntakeError(f"{path}: line {n} has seq_no {entry.seq_no}, expected {expected}")
        entries.append(entry)
    return entries, good


@dataclass(frozen=True)
class Segment:
    """A sealed part of the log: entries `first_seq`..`last_seq`, the last of which the
    journal recorded at `last_ts`."""
    path: Path
    first_seq: int
    last_seq: int
    last_ts: int

    def load(self) -> list[IntakeEntry]:
        return _parse(self.path, self.path.read_bytes(), self.first_seq)[0]


def segment_name(last_seq: int, last_ts: int) -> str:
    return f"seg-{last_seq:012d}-{last_ts}.jsonl"


def segments(symbol_dir: str | Path) -> list[Segment]:
    """The sealed segments of one symbol, oldest first."""
    found = []
    seg_dir = Path(symbol_dir) / SEGMENT_DIR
    if seg_dir.is_dir():
        for p in seg_dir.iterdir():
            m = _SEGMENT.match(p.name)
            if m:
                found.append((int(m[1]), int(m[2]), p))
    found.sort()
    out, first = [], 1
    for last, ts, p in found:
        out.append(Segment(p, first, last, ts))
        first = last + 1
    return out


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def drop_segments_after(symbol_dir: str | Path, seq_no: int, *, fsync: bool = True) -> int:
    """Removes sealed entries beyond `seq_no` (the journal lost them, so they are orphans)
    and returns how many. A segment that straddles it is cut and renamed."""
    dropped = 0
    for seg in reversed(segments(symbol_dir)):
        if seg.last_seq <= seq_no:
            break
        if seg.first_seq > seq_no:
            dropped += seg.last_seq - seg.first_seq + 1
            seg.path.unlink()
            continue
        keep = [e for e in seg.load() if e.seq_no <= seq_no]
        dropped += seg.last_seq - seq_no
        new = seg.path.with_name(segment_name(seq_no, seg.last_ts))
        tmp = new.with_suffix(".tmp")
        tmp.write_bytes(b"".join(e.line() for e in keep))
        if fsync:
            with tmp.open("rb") as f:
                os.fsync(f.fileno())
        os.replace(tmp, new)
        seg.path.unlink()
    if dropped and fsync:
        _sync_dir(Path(symbol_dir) / SEGMENT_DIR)
    return dropped


def _write_atomic(path: Path, data: bytes, fsync: bool) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    if fsync:
        with tmp.open("rb") as f:
            os.fsync(f.fileno())
    os.replace(tmp, path)


def trim_sealed_prefix(path: str | Path, first: int, *, fsync: bool = True) -> int:
    """Finishes a split or rotation a crash interrupted: drops the active log's leading
    entries that a sealed segment already holds (they are copies). Returns how many."""
    path = Path(path)
    if not path.exists():
        return 0
    with path.open("rb") as f:
        head = f.readline()
    if not head.endswith(b"\n") or json.loads(head)["seq_no"] >= first:
        return 0
    data = path.read_bytes()
    entries, good = _parse(path, data, json.loads(head)["seq_no"])
    keep = [e for e in entries if e.seq_no >= first]
    _write_atomic(path, b"".join(e.line() for e in keep) + data[good:], fsync)
    return len(entries) - len(keep)


def split_into_segments(symbol_dir: str | Path, ends: list[tuple[int, int]], *, fsync: bool = True) -> None:
    """Turns an active log from before segments (entries from seq_no 1) into sealed
    segments ending at each (last_seq, last_ts) of `ends`, ascending, and an active log
    with the rest. Line n holds seq_no n, so the bytes are split by line and only the
    lines at the cuts are parsed (to check that). Segments are written first, so a crash
    leaves copies that trim_sealed_prefix removes on the next open."""
    symbol_dir = Path(symbol_dir)
    path = symbol_dir / "intake.jsonl"
    data = path.read_bytes()
    good = data.rfind(b"\n") + 1
    lines = data[:good].splitlines(keepends=True)
    seg_dir = symbol_dir / SEGMENT_DIR
    seg_dir.mkdir(exist_ok=True)
    start = 0
    for last_seq, last_ts in ends:
        for n in (start + 1, last_seq):
            if n > len(lines) or json.loads(lines[n - 1])["seq_no"] != n:
                raise IntakeError(f"{path}: line {n} does not hold seq_no {n}")
        _write_atomic(seg_dir / segment_name(last_seq, last_ts), b"".join(lines[start:last_seq]), fsync)
        start = last_seq
    if fsync:
        _sync_dir(seg_dir)
    _write_atomic(path, b"".join(lines[start:]) + data[good:], fsync)
    if fsync:
        _sync_dir(symbol_dir)


class IntakeLog:
    def __init__(self, path: Path, fd: int, size: int, entries: list[IntakeEntry], first: int) -> None:
        self.path = path
        self._fd = fd
        self.size = size
        self.durable_size = 0
        self.first = first  # the seq_no the active log starts at
        self.entries = entries  # as loaded at open; not kept up to date

    @staticmethod
    def load(path: str | Path, first: int = 1) -> list[IntakeEntry]:
        path = Path(path)
        return _parse(path, path.read_bytes(), first)[0] if path.exists() else []

    @classmethod
    def open(cls, path: str | Path, first: int = 1) -> IntakeLog:
        """Opens (or creates) the log for appending; a torn final line is cut off."""
        path = Path(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        data = path.read_bytes()
        entries, good = _parse(path, data, first)
        if good < len(data):
            os.ftruncate(fd, good)
        os.lseek(fd, good, os.SEEK_SET)
        return cls(path, fd, good, entries, first)

    def append(self, entries: list[IntakeEntry]) -> None:
        """One write(2) for the whole batch."""
        data = b"".join(e.line() for e in entries)
        view = memoryview(data)
        while view:
            n = os.write(self._fd, view)
            view = view[n:]
        self.size += len(data)

    def sync(self) -> None:
        (getattr(os, "fdatasync", None) or os.fsync)(self._fd)
        self.durable_size = self.size

    def truncate_after(self, seq_no: int) -> None:
        """Drops the entries after `seq_no` (orphans of a crash) and syncs the cut."""
        data = self.path.read_bytes()
        entries, _ = _parse(self.path, data, self.first)
        keep = sum(len(e.line()) for e in entries if e.seq_no <= seq_no)
        os.ftruncate(self._fd, keep)
        os.lseek(self._fd, keep, os.SEEK_SET)
        self.size = keep
        self.sync()

    def rotate(self, last_seq: int, last_ts: int, *, fsync: bool = True) -> Segment:
        """Seals the log, which must end at `last_seq`, as a segment and starts an empty
        one. Renames are atomic, so a crash leaves either the old log in place or the
        segment plus a missing (recreated on open) or empty log."""
        if fsync:
            self.sync()
        seg_dir = self.path.parent / SEGMENT_DIR
        seg_dir.mkdir(exist_ok=True)
        target = seg_dir / segment_name(last_seq, last_ts)
        os.close(self._fd)
        os.replace(self.path, target)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o644)
        if fsync:
            _sync_dir(seg_dir)
            _sync_dir(self.path.parent)
        first, self.first = self.first, last_seq + 1
        self.size = self.durable_size = 0
        return Segment(target, first, last_seq, last_ts)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
