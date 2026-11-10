"""Reading a symbol's journal for the projection (M10). The projector never trusts
Redis or the matcher for what happened: it reads the journal (the source of truth) and
regenerates the events with its own engine, which determinism makes identical to the
matcher's. Client order ids come from the intake log: the sealed segments, then the
active log (which the matcher replaces at each snapshot, so it is followed by inode).
The journal and intake log are only read (a read-only mount in compose)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import mfengine as mf

from services.common.events import command_to_dict, event_to_dict
from services.matcher.intake import IntakeEntry, segments
from services.projector.projection import ProjectedRecord


class SymbolSource:
    def __init__(self, data_dir: str | Path, symbol: str) -> None:
        self.symbol = symbol
        self.dir = Path(data_dir) / symbol
        self.journal = self.dir / "journal.mfj"
        self.engine = mf.Engine()
        self.started_from_snapshot = 0
        self._offset = 0  # journal offset after the last record read (0: start of file)
        self._last_seq = 0
        self._last_ts: int | None = None
        self._intake: dict[int, IntakeEntry] = {}
        self._intake_offset = 0  # into the active log identified by _intake_inode
        self._intake_inode: int | None = None
        self.failed: str | None = None

    def last_journal_seq(self) -> int:
        if not self.journal.exists():
            return 0
        reader = mf.JournalReader(self.journal)
        try:
            for _ in reader:
                pass
        except mf.JournalError as e:
            if e.kind != mf.JournalErrorKind.TRUNCATED:
                raise
        return int(reader.last_seq_no)

    def needs_rebuild(self, cursor: int) -> bool:
        """The projection has seen records the journal no longer has (an OS crash lost
        unsynced records that the projector had already read)."""
        return cursor > self.last_journal_seq()

    def start(self, cursor: int) -> None:
        """Rebuilds engine state up to `cursor` (from the newest snapshot at or before it)
        without producing records; poll() continues after it."""
        self.engine = mf.Engine()
        self._offset, self._last_seq, self._last_ts = 0, 0, None
        self.started_from_snapshot = 0
        for path in sorted((self.dir / "snapshots").glob("snap-*.mfsnap"), reverse=True):
            try:
                snap = mf.read_snapshot_file(path)
                seq = snap.last_seq_no or 0
                if 0 < seq <= cursor:
                    self.engine = mf.Engine.from_snapshot(snap)
                    self._offset, self._last_seq, self._last_ts = snap.journal_offset, seq, snap.last_timestamp
                    self.started_from_snapshot = seq
                    break
            except (mf.MarketForgeError, OSError):
                continue
        while self._last_seq < cursor:
            if not self._read(cursor - self._last_seq, keep=False):
                raise RuntimeError(f"{self.symbol}: journal ends at {self._last_seq}, cursor is {cursor}")

    def _load_intake(self, need: int) -> None:
        """Loads the entries from seq_no `need` on: from the active log (the usual case:
        only its unread part), else from the sealed segment that holds it."""
        if self._read_active(need):
            return
        for seg in segments(self.dir):
            if seg.last_seq >= need:
                self._intake.update((e.seq_no, e) for e in seg.load() if e.seq_no >= need)
                return

    def _read_active(self, need: int) -> bool:
        path = self.dir / "intake.jsonl"
        try:
            f = path.open("rb")
        except FileNotFoundError:  # the matcher is between sealing and recreating it
            return False
        with f:
            inode = os.fstat(f.fileno()).st_ino
            if inode != self._intake_inode:  # rotated: a new active log
                self._intake_inode, self._intake_offset = inode, 0
            f.seek(self._intake_offset)
            data = f.read()
        good = data.rfind(b"\n") + 1
        for line in data[:good].splitlines():
            entry = IntakeEntry(**json.loads(line))
            if entry.seq_no >= need:
                self._intake[entry.seq_no] = entry
        self._intake_offset += good
        return need in self._intake

    def _intake_entry(self, seq_no: int) -> IntakeEntry:
        for _ in range(3):  # a rotation can move the entry between looking and reading
            if seq_no in self._intake:
                return self._intake.pop(seq_no)
            self._load_intake(seq_no)
        raise RuntimeError(f"{self.symbol}: no intake entry for journal record {seq_no}")

    def _read(self, max_records: int, keep: bool = True) -> list[ProjectedRecord]:
        if self.failed or not self.journal.exists():
            return []
        reader = mf.JournalReader(self.journal)
        if self._offset:
            reader.seek(self._offset, self._last_seq, self._last_ts if self._last_ts is not None else -(2**63))
        out: list[ProjectedRecord] = []
        try:
            for rec in reader:
                try:
                    events = self.engine.process(rec.command, rec.timestamp)
                except mf.MarketForgeError as e:  # the matcher halted this symbol at the same record
                    self.failed = f"engine failed at seq_no {rec.seq_no}: {e}"
                    break
                self._offset, self._last_seq, self._last_ts = reader.offset, rec.seq_no, rec.timestamp
                if keep:
                    out.append(ProjectedRecord(seq_no=rec.seq_no, timestamp=rec.timestamp,
                                               command=command_to_dict(rec.command),
                                               events=[event_to_dict(e) for e in events],
                                               intake=self._intake_entry(rec.seq_no)))
                else:
                    out.append(None)  # type: ignore[arg-type]
                if len(out) >= max_records:
                    break
        except mf.JournalError as e:
            if e.kind != mf.JournalErrorKind.TRUNCATED:  # a torn tail: the matcher is mid-append
                raise
        return out

    def poll(self, max_records: int) -> list[ProjectedRecord]:
        """The next records after the last one returned (at most max_records)."""
        return self._read(max_records)
