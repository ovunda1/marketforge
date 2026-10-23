"""Journal, replay, snapshots and risk config from Python (M7), cross-checked against the
CLI tools: a journal written from Python replays to the hash of the Python-driven
stream in mf-replay and in mfengine.replay, from the start or from a snapshot."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

import mfengine as mf
from mf_helpers import command_output, drive, timed
from risk_flow import generate as risk_generate

RISK_SEED = 20261021
HEADER_SIZE = 16
CANCEL_RECORD = 33  # bytes (docs/journal-format.md)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_journal(path: Path, commands: list[tuple[mf.Command, int]]) -> Path:
    with mf.JournalWriter(path) as writer:
        for command, ts in commands:
            writer.append(mf.JournalRecord(timestamp=ts, command=command))
        assert writer.last_seq_no == commands[-1][0].seq_no
        assert writer.bytes_written == path.stat().st_size
    return path


def mf_replay(tools: dict[str, Path], *args: str | Path) -> str:
    result = subprocess.run([str(tools["mf-replay"]), *map(str, args)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("sha256="), result.stdout
    return lines[0].removeprefix("sha256=")


@pytest.fixture(scope="module")
def flow() -> list[tuple[mf.Command, int]]:
    return timed(risk_generate(RISK_SEED, 5_000))


def test_python_journal_replays_to_the_python_stream_hash(
    flow: list[tuple[mf.Command, int]], tools: dict[str, Path], tmp_path: Path
) -> None:
    journal = write_journal(tmp_path / "run.mfj", flow)
    expected = sha(drive(flow))
    assert mf_replay(tools, journal) == expected
    result = mf.replay(journal)
    assert (result.sha256, result.commands, result.last_seq_no) == (expected, len(flow),
                                                                    flow[-1][0].seq_no)
    assert result.events is None
    assert mf.replay(journal, capture_events=True).events == drive(flow)


def test_reader_round_trips_the_records(flow: list[tuple[mf.Command, int]], tmp_path: Path) -> None:
    journal = write_journal(tmp_path / "run.mfj", flow)
    records = list(mf.JournalReader(journal))
    assert [(r.command, r.timestamp) for r in records] == flow
    assert records[0].seq_no == flow[0][0].seq_no
    assert mf.JournalReader(journal).read_all() == records
    reader = mf.JournalReader(journal)
    assert reader.offset == HEADER_SIZE and reader.last_seq_no == 0
    next(reader)
    assert reader.last_seq_no == 1


def test_writer_refuses_gaps_and_existing_files(tmp_path: Path) -> None:
    path = tmp_path / "j.mfj"
    with mf.JournalWriter(path) as writer:
        writer.append(mf.JournalRecord(timestamp=0, command=mf.Cancel(seq_no=1, order_id=1)))
        with pytest.raises(ValueError):
            writer.append(mf.JournalRecord(timestamp=0, command=mf.Cancel(seq_no=3, order_id=1)))
    with pytest.raises(ValueError, match="closed"):
        writer.append(mf.JournalRecord(timestamp=0, command=mf.Cancel(seq_no=2, order_id=1)))
    with pytest.raises(mf.JournalError) as info:
        mf.JournalWriter(path)
    assert info.value.kind is mf.JournalErrorKind.IO


def cancels(n: int) -> list[tuple[mf.Command, int]]:
    return [(mf.Cancel(seq_no=i, order_id=i), i) for i in range(1, n + 1)]


def damaged(tmp_path: Path, name: str, edit) -> Path:
    data = bytearray(write_journal(tmp_path / "good.mfj", cancels(5)).read_bytes())
    (tmp_path / "good.mfj").unlink()
    bad = tmp_path / name
    bad.write_bytes(bytes(edit(data)))
    return bad


def flip(at: int):
    def edit(data: bytearray) -> bytearray:
        data[at] ^= 0x01
        return data
    return edit


@pytest.mark.parametrize("name,edit,kind", [
    ("truncated.mfj", lambda d: d[:-5], mf.JournalErrorKind.TRUNCATED),
    ("corrupt.mfj", flip(HEADER_SIZE + CANCEL_RECORD + 10), mf.JournalErrorKind.CORRUPT),
    ("gap.mfj", lambda d: d[: HEADER_SIZE + CANCEL_RECORD] + d[HEADER_SIZE + 2 * CANCEL_RECORD :],
     mf.JournalErrorKind.SEQUENCE_GAP),
])
def test_damaged_journals_raise_journal_error_with_its_kind(tmp_path: Path, name: str, edit, kind) -> None:
    bad = damaged(tmp_path, name, edit)
    with pytest.raises(mf.JournalError) as info:
        list(mf.JournalReader(bad))
    assert info.value.kind is kind
    assert name in str(info.value)
    with pytest.raises(mf.JournalError) as info:
        mf.replay(bad)
    assert info.value.kind is kind


def test_bad_header_is_reported_on_open(tmp_path: Path) -> None:
    bad = damaged(tmp_path, "header.mfj", flip(0))
    with pytest.raises(mf.JournalError) as info:
        mf.JournalReader(bad)
    assert info.value.kind is mf.JournalErrorKind.BAD_HEADER
    with pytest.raises(mf.JournalError) as info:
        mf.JournalReader(tmp_path / "missing.mfj")
    assert info.value.kind is mf.JournalErrorKind.IO


def test_snapshot_plus_tail_from_python(
    flow: list[tuple[mf.Command, int]], tools: dict[str, Path], tmp_path: Path
) -> None:
    journal = write_journal(tmp_path / "run.mfj", flow)
    cut = 2_000
    # Drive the first part, snapshot with the offset the reader reports, then compare
    # the tail three ways: mf-replay, mfengine.replay, and an engine restored in Python.
    engine = mf.Engine()
    reader = mf.JournalReader(journal)
    for _ in range(cut):
        record = next(reader)
        engine.process(record.command, timestamp=record.timestamp)
    snap = engine.snapshot(last_timestamp=record.timestamp, journal_offset=reader.offset)
    assert (snap.last_seq_no, snap.last_timestamp, snap.journal_offset) == (
        record.seq_no, record.timestamp, reader.offset)
    path = tmp_path / "snap.mfsnap"
    mf.write_snapshot_file(path, snap)
    assert mf.read_snapshot_file(path) == snap

    full = drive(flow).decode()
    marker = f"BOOK seq={record.seq_no} "
    suffix = full[full.index("\n", full.index(marker)) + 1 :].encode()
    assert mf_replay(tools, journal, "--snapshot", path) == sha(suffix)
    assert mf.replay(journal, snapshot=path).sha256 == sha(suffix)

    restored = mf.Engine.from_snapshot(snap)
    tail = [(r.command, r.timestamp) for r in reader]
    assert drive(tail, restored) == suffix


def test_replay_writes_periodic_snapshots(flow: list[tuple[mf.Command, int]], tmp_path: Path) -> None:
    journal = write_journal(tmp_path / "run.mfj", flow)
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    result = mf.replay(journal, snapshot_every=2_000, snapshot_dir=snaps)
    assert [Path(p).name for p in result.snapshots_written] == [
        f"snap-{n:012d}.mfsnap" for n in range(2_000, len(flow) + 1, 2_000)]


def test_restored_engine_refuses_time_before_the_snapshot(flow: list[tuple[mf.Command, int]]) -> None:
    engine = mf.Engine()
    for command, ts in flow[:100]:
        engine.process(command, timestamp=ts)
    last_ts = flow[99][1]
    restored = mf.Engine.from_snapshot(engine.snapshot(last_timestamp=last_ts + 5))
    nxt = mf.Cancel(seq_no=flow[99][0].seq_no + 1, order_id=1)
    with pytest.raises(mf.SequenceError):
        restored.process(nxt, timestamp=last_ts + 4)
    restored.process(nxt, timestamp=last_ts + 5)


def test_snapshot_bytes_round_trip_and_errors(flow: list[tuple[mf.Command, int]], tmp_path: Path) -> None:
    engine = mf.Engine()
    for command, ts in flow[:500]:
        engine.process(command, timestamp=ts)
    snap = engine.snapshot(last_timestamp=flow[499][1], journal_offset=123)
    data = mf.encode_snapshot(snap)
    assert isinstance(data, bytes)
    assert mf.decode_snapshot(data) == snap
    assert mf.encode_snapshot(mf.decode_snapshot(data)) == data
    with pytest.raises(mf.SnapshotError):
        mf.decode_snapshot(data[:-1])
    with pytest.raises(mf.SnapshotError):
        mf.decode_snapshot(b"MFSNAPSH junk")
    with pytest.raises(mf.SnapshotError):
        mf.read_snapshot_file(tmp_path / "missing.mfsnap")


def test_snapshot_defaults_to_the_engines_last_timestamp() -> None:
    engine = mf.Engine()
    assert engine.last_timestamp is None
    engine.process(mf.Cancel(seq_no=1, order_id=1), timestamp=77)
    snap = engine.snapshot()
    assert (snap.last_timestamp, snap.journal_offset) == (77, 0)


def test_restored_engine_continues_identically(flow: list[tuple[mf.Command, int]]) -> None:
    engine = mf.Engine()
    for command, ts in flow[:1_000]:
        engine.process(command, timestamp=ts)
    restored = mf.Engine.from_snapshot(engine.snapshot())
    for command, ts in flow[1_000:2_000]:
        assert restored.process(command, timestamp=ts) == engine.process(command, timestamp=ts)
    assert command_output(restored, [], 0) == command_output(engine, [], 0)


# --- Risk config ---------------------------------------------------------------------


def test_load_risk_config_gives_admin_commands_in_a_fixed_order(tmp_path: Path) -> None:
    config = tmp_path / "risk.yaml"
    config.write_text(
        "instrument: {max_qty: 40}\n"
        "accounts:\n"
        "  7: {credit: 10}\n"
        "  2: {max_open_orders: 3, rate: {per_sec: 5, burst: 2}}\n"
        "  default: {position_limit: 60}\n"
    )
    commands = mf.load_risk_config(config)
    assert commands == [
        mf.SetInstrumentLimits(seq_no=0, limits=mf.InstrumentLimits(max_qty=40)),
        mf.SetAccountLimits(seq_no=0, account_id=None, limits=mf.AccountLimits(position_limit=60)),
        mf.SetAccountLimits(seq_no=0, account_id=2, limits=mf.AccountLimits(
            max_open_orders=3, rate=mf.RateLimit(per_sec=5, burst=2))),
        mf.SetAccountLimits(seq_no=0, account_id=7, limits=mf.AccountLimits(credit=10)),
    ]
    assert mf.parse_risk_config(config.read_text()) == commands
    assert mf.load_risk_config(str(config)) == commands  # str or PathLike


def test_bad_config_raises_config_error_naming_the_key(tmp_path: Path) -> None:
    with pytest.raises(mf.ConfigError, match="max_qty"):
        mf.parse_risk_config("instrument: {max_qty: -1}\n")
    with pytest.raises(mf.ConfigError, match="bogus"):
        mf.parse_risk_config("bogus: 1\n")
    with pytest.raises(mf.ConfigError, match="missing.yaml"):
        mf.load_risk_config(tmp_path / "missing.yaml")


# --- Resume and sync (M10: the live matcher continues its journal after a restart) ---


def test_resume_continues_a_journal_and_trims_a_torn_tail(
    flow: list[tuple[mf.Command, int]], tmp_path: Path
) -> None:
    once = write_journal(tmp_path / "once.mfj", flow).read_bytes()
    path = write_journal(tmp_path / "twice.mfj", flow[:3000])
    record = mf.JournalRecord(timestamp=flow[3000][1], command=flow[3000][0])
    with path.open("ab") as f:  # a crash in the middle of the next append
        f.write(b"\x3e\x00\x00\x00\x01\x02")

    writer = mf.JournalWriter.resume(path)
    assert writer.trimmed_bytes == 6
    assert writer.last_seq_no == flow[2999][0].seq_no
    assert writer.bytes_written == path.stat().st_size
    with writer:
        writer.append(record)
        for command, ts in flow[3001:]:
            writer.append(mf.JournalRecord(timestamp=ts, command=command))
        writer.sync()
    assert path.read_bytes() == once
    with pytest.raises(ValueError, match="closed"):
        writer.sync()


def test_new_journal_has_nothing_trimmed_and_can_sync(tmp_path: Path) -> None:
    with mf.JournalWriter(tmp_path / "j.mfj") as writer:
        assert writer.trimmed_bytes == 0
        writer.append(mf.JournalRecord(timestamp=1, command=mf.Cancel(seq_no=1, order_id=1)))
        writer.sync()


def test_resume_refuses_damage_other_than_a_torn_tail(
    flow: list[tuple[mf.Command, int]], tmp_path: Path
) -> None:
    path = write_journal(tmp_path / "j.mfj", flow[:10])
    data = bytearray(path.read_bytes())
    data[HEADER_SIZE + 30] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(mf.JournalError) as info:
        mf.JournalWriter.resume(path)
    assert info.value.kind == mf.JournalErrorKind.CORRUPT
    assert path.read_bytes() == bytes(data)
    with pytest.raises(mf.JournalError) as missing:
        mf.JournalWriter.resume(tmp_path / "missing.mfj")
    assert missing.value.kind == mf.JournalErrorKind.IO
