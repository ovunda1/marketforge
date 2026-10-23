"""Reading a symbol's journal for the projection (M10). The projector never trusts
Redis or the matcher for what happened: it reads the journal (the source of truth) and
regenerates the events with its own engine, which determinism makes identical to the
matcher's. Client order ids come from the intake log. The journal and intake log are
only read (a read-only mount in compose)."""

from __future__ import annotations

import json
from pathlib import Path

import mfengine as mf

from services.common.events import command_to_dict, event_to_dict
from services.matcher.intake import IntakeEntry
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
        self._intake_offset = 0
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

    def _load_intake(self) -> None:
        path = self.dir / "intake.jsonl"
        if not path.exists():
            return
        with path.open("rb") as f:
            f.seek(self._intake_offset)
            data = f.read()
        good = data.rfind(b"\n") + 1
        for line in data[:good].splitlines():
            entry = IntakeEntry(**json.loads(line))
            self._intake[entry.seq_no] = entry
        self._intake_offset += good

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
                    if rec.seq_no not in self._intake:
                        self._load_intake()
                    out.append(ProjectedRecord(seq_no=rec.seq_no, timestamp=rec.timestamp,
                                               command=command_to_dict(rec.command),
                                               events=[event_to_dict(e) for e in events],
                                               intake=self._intake.pop(rec.seq_no)))
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
