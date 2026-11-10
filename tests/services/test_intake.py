"""The intake log (M10): the matcher's per-symbol sidecar to the journal, holding what the
journal's binary format has no field for (request id, client_order_id or idempotency
key, payload hash, the order id the matcher assigned). Written and fsynced before the
journal record it describes."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.matcher.intake import IntakeEntry, IntakeError, IntakeLog, drop_segments_after, segments


def entry(seq: int, **kw) -> IntakeEntry:
    base = dict(seq_no=seq, request_id=f"r{seq}", op="new", account_id=7, key=f"k{seq}",
                payload_hash="ab" * 32, order_id=100 + seq)
    return IntakeEntry(**(base | kw))


def test_entries_round_trip_and_sync_tracks_the_durable_size(tmp_path: Path) -> None:
    log = IntakeLog.open(tmp_path / "intake.jsonl")
    log.append([entry(1), entry(2, op="cancel", key=None, order_id=101)])
    assert log.durable_size == 0
    log.sync()
    assert log.durable_size == log.size == (tmp_path / "intake.jsonl").stat().st_size
    log.close()
    assert IntakeLog.load(tmp_path / "intake.jsonl") == [entry(1), entry(2, op="cancel", key=None, order_id=101)]


def test_a_torn_final_line_is_ignored_on_load_and_trimmed_on_open(tmp_path: Path) -> None:
    path = tmp_path / "intake.jsonl"
    log = IntakeLog.open(path)
    log.append([entry(1)])
    log.close()
    good = path.stat().st_size
    with path.open("ab") as f:
        f.write(b'{"seq_no": 2, "request_')
    assert IntakeLog.load(path) == [entry(1)]
    log = IntakeLog.open(path)
    assert path.stat().st_size == good
    log.append([entry(2)])
    log.close()
    assert [e.seq_no for e in IntakeLog.load(path)] == [1, 2]


def test_truncate_after_drops_entries_beyond_the_journal(tmp_path: Path) -> None:
    path = tmp_path / "intake.jsonl"
    log = IntakeLog.open(path)
    log.append([entry(i) for i in range(1, 6)])
    log.truncate_after(3)
    log.append([entry(4, key="again")])
    log.close()
    assert [(e.seq_no, e.key) for e in IntakeLog.load(path)] == [(1, "k1"), (2, "k2"), (3, "k3"), (4, "again")]


def test_damage_before_the_last_line_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "intake.jsonl"
    log = IntakeLog.open(path)
    log.append([entry(1), entry(2)])
    log.close()
    data = path.read_bytes()
    path.write_bytes(data.replace(b'"seq_no": 1', b'"seq_no": X', 1))
    with pytest.raises(IntakeError):
        IntakeLog.load(path)


def test_entries_must_be_numbered_without_gaps(tmp_path: Path) -> None:
    path = tmp_path / "intake.jsonl"
    log = IntakeLog.open(path)
    log.append([entry(1), entry(3)])
    log.close()
    with pytest.raises(IntakeError, match="seq_no"):
        IntakeLog.load(path)


# --- Segments (after M11b) -------------------------------------------------------------------------

def test_rotate_seals_the_log_as_a_segment_and_continues_numbering(tmp_path: Path) -> None:
    log = IntakeLog.open(tmp_path / "intake.jsonl")
    log.append([entry(1), entry(2)])
    seg = log.rotate(2, 5000)
    assert (seg.path.name, seg.first_seq, seg.last_seq, seg.last_ts) == ("seg-000000000002-5000.jsonl", 1, 2, 5000)
    assert (tmp_path / "intake.jsonl").stat().st_size == 0 and log.first == 3
    log.append([entry(3)])
    log.rotate(3, 6000)
    log.append([entry(4)])
    log.close()
    assert [(s.first_seq, s.last_seq) for s in segments(tmp_path)] == [(1, 2), (3, 3)]
    assert [e.seq_no for s in segments(tmp_path) for e in s.load()] == [1, 2, 3]
    assert IntakeLog.load(tmp_path / "intake.jsonl", 4) == [entry(4)]
    with pytest.raises(IntakeError, match="expected 1"):
        IntakeLog.load(tmp_path / "intake.jsonl")  # the active log no longer starts at 1


def test_drop_segments_after_cuts_a_straddling_segment_and_removes_later_ones(tmp_path: Path) -> None:
    log = IntakeLog.open(tmp_path / "intake.jsonl")
    for first, last in ((1, 3), (4, 6), (7, 8)):
        log.append([entry(i) for i in range(first, last + 1)])
        log.rotate(last, 1000 * last)
    log.close()
    assert drop_segments_after(tmp_path, 4) == 4
    assert [(s.first_seq, s.last_seq, s.last_ts) for s in segments(tmp_path)] == [(1, 3, 3000), (4, 4, 6000)]
    assert [e.seq_no for s in segments(tmp_path) for e in s.load()] == [1, 2, 3, 4]
    assert drop_segments_after(tmp_path, 4) == 0
