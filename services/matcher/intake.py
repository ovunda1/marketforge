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
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


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


def _parse(path: Path, data: bytes) -> tuple[list[IntakeEntry], int]:
    """Entries and the byte length of the complete lines (a torn last line is left out)."""
    entries: list[IntakeEntry] = []
    good = data.rfind(b"\n") + 1
    for n, line in enumerate(data[:good].splitlines(), 1):
        try:
            entry = IntakeEntry(**json.loads(line))
        except (ValueError, TypeError) as e:
            raise IntakeError(f"{path}: line {n} is damaged: {e}") from None
        expected = entries[-1].seq_no + 1 if entries else 1
        if entry.seq_no != expected:
            raise IntakeError(f"{path}: line {n} has seq_no {entry.seq_no}, expected {expected}")
        entries.append(entry)
    return entries, good


class IntakeLog:
    def __init__(self, path: Path, fd: int, size: int, entries: list[IntakeEntry]) -> None:
        self.path = path
        self._fd = fd
        self.size = size
        self.durable_size = 0
        self.entries = entries  # as loaded at open; not kept up to date

    @staticmethod
    def load(path: str | Path) -> list[IntakeEntry]:
        path = Path(path)
        return _parse(path, path.read_bytes())[0] if path.exists() else []

    @classmethod
    def open(cls, path: str | Path) -> IntakeLog:
        """Opens (or creates) the log for appending; a torn final line is cut off."""
        path = Path(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        data = path.read_bytes()
        entries, good = _parse(path, data)
        if good < len(data):
            os.ftruncate(fd, good)
        os.lseek(fd, good, os.SEEK_SET)
        return cls(path, fd, good, entries)

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
        entries, _ = _parse(self.path, data)
        keep = sum(len(e.line()) for e in entries if e.seq_no <= seq_no)
        os.ftruncate(self._fd, keep)
        os.lseek(self._fd, keep, os.SEEK_SET)
        self.size = keep
        self.sync()

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
