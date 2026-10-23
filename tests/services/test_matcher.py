"""The single matcher (M10): the one process that owns the engines, sequencers and
journals. Commands arrive as batches of gateway requests; each batch is made durable
(intake log, then journal, both fsynced) before any reply or market data leaves."""

from __future__ import annotations

from pathlib import Path

import mfengine as mf
import pytest

from svc_helpers import (FakeClock, admin, book, cancel, intake_path, journal_events,
                         journal_path, journaled_keys, new, open_matcher, power_loss, random_flow, replace, run)
from services.common.instruments import Instrument
from services.matcher.core import MatcherLocked
from services.matcher.intake import IntakeLog


def test_replies_carry_exactly_the_events_a_journal_replay_produces(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    flow = random_flow(1, 3000, symbols=["AAA", "BBB"])
    replies = run(m, flow, batch=13)
    m.close()

    assert all(r.status in ("ok", "rejected") for r in replies), {r.code for r in replies}
    for symbol in ("AAA", "BBB"):
        replayed = journal_events(journal_path(tmp_path, symbol))
        sent = sorted((r.seq_no, r.events) for r in replies if r.symbol == symbol)
        assert sent == replayed
        assert [s for s, _ in replayed] == list(range(1, len(replayed) + 1))  # gapless


def test_every_journal_replays_with_mf_replay(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, random_flow(2, 500, symbols=["AAA", "BBB"]))
    m.close()
    for symbol in ("AAA", "BBB"):
        assert mf.replay(journal_path(tmp_path, symbol)).commands > 0


def test_order_ids_are_unique_across_symbols_and_cancels_route_by_id(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    a, b = run(m, [new(1, "AAA", "buy", 100, 5), new(1, "BBB", "buy", 100, 5)])
    assert (a.order_id, b.order_id) == (1, 2)
    [c] = run(m, [cancel(1, 2)])
    assert (c.status, c.symbol, c.events[0]["type"]) == ("ok", "BBB", "cancelled")
    [r] = run(m, [replace(1, 1, 101, 7)])
    assert (r.symbol, r.events[0]["type"]) == ("AAA", "replaced")


def test_errors_are_never_journaled(tmp_path: Path) -> None:
    clock = FakeClock()
    m = open_matcher(tmp_path, clock)
    [placed] = run(m, [new(1, "AAA", "buy", 100, 5)])
    replies = run(m, [
        cancel(2, placed.order_id),                       # not the owner
        cancel(1, 999),                                   # never existed
        new(1, "ZZZ", "buy", 100, 5),                     # unknown symbol
        new(1, "AAA", "buy", 100, 5, deadline=clock.now - 1),  # the gateway gave up already
    ])
    assert [(r.status, r.code) for r in replies] == [
        ("error", "NOT_FOUND"), ("error", "NOT_FOUND"), ("error", "UNKNOWN_SYMBOL"), ("error", "EXPIRED")]
    assert m.last_seq_no("AAA") == 1


def test_an_engine_reject_is_journaled_and_reported_with_its_reason(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    [r] = run(m, [new(1, "AAA", "buy", 100, 0)])
    assert (r.status, r.reason, r.seq_no, r.order_id) == ("rejected", "INVALID_QTY", 1, 1)
    [c] = run(m, [cancel(1, 1)])  # known to the matcher, not open in the book
    assert (c.status, c.reason) == ("rejected", "UNKNOWN_ORDER")


# --- Idempotency ---------------------------------------------------------------------------------

def test_a_retried_client_order_id_returns_the_original_order(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    first = run(m, [new(1, "AAA", "buy", 100, 5, "abc")])[0]
    again = run(m, [new(1, "AAA", "buy", 100, 5, "abc")])[0]
    assert (again.status, again.replayed, again.order_id, again.seq_no) == ("ok", True, first.order_id, first.seq_no)
    assert again.events == []
    assert m.last_seq_no("AAA") == 1


def test_a_client_order_id_reused_with_a_different_order_is_a_conflict(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 5, "abc")])
    [r] = run(m, [new(1, "AAA", "buy", 101, 5, "abc")])
    assert (r.status, r.code) == ("error", "IDEMPOTENCY_CONFLICT")
    [other] = run(m, [new(2, "AAA", "buy", 100, 5, "abc")])  # keys are per account
    assert (other.status, other.replayed) == ("ok", False)


def test_duplicates_inside_one_batch_make_one_order(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    replies = m.process_batch([new(1, "AAA", "buy", 100, 5, "dup") for _ in range(50)]).replies
    assert len({r.order_id for r in replies}) == 1
    assert [r.replayed for r in replies] == [False] + [True] * 49
    assert m.last_seq_no("AAA") == 1


def test_cancel_and_replace_retries_with_an_idempotency_key_apply_once(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 5)])
    r1, r2 = run(m, [replace(1, 1, 99, 9, key="k1"), replace(1, 1, 99, 9, key="k1")], batch=1)
    assert (r1.replayed, r2.replayed, r2.seq_no) == (False, True, r1.seq_no)
    c1, c2 = run(m, [cancel(1, 1, key="k2"), cancel(1, 1, key="k2")], batch=1)
    assert (c1.status, c2.replayed, c2.seq_no) == ("ok", True, c1.seq_no)
    assert m.last_seq_no("AAA") == 3


# --- Durability: the order of writes -------------------------------------------------------------

def test_intake_is_synced_before_the_first_journal_write_and_the_journal_before_return(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    m.trace = []
    m.process_batch([new(1, "AAA", "buy", 100, 5), new(2, "BBB", "sell", 100, 5), new(2, "AAA", "sell", 100, 3)])
    ops = [t.split(":")[0] for t in m.trace]
    first_journal = ops.index("journal.append")
    assert ops.count("intake.sync") == 2 and max(i for i, o in enumerate(ops) if o == "intake.sync") < first_journal
    assert ops.count("journal.sync") == 2
    last_engine = max(i for i, o in enumerate(ops) if o == "engine")
    assert min(i for i, o in enumerate(ops) if o == "journal.sync") > last_engine
    assert ops[-1] == "journal.sync"  # nothing is returned (so nothing published) before it


class Crash(Exception):
    """The process dies here. The hook runs after "intake.write", "journal.append" and
    "engine", and before "intake.sync" and "journal.sync"."""


CRASH_POINTS = ["intake.write", "intake.sync", "journal.append", "engine", "journal.sync"]


def crash_at(point: str, nth: int = 2):
    count = {"n": 0}

    def hook(event: str) -> None:
        if event.split(":")[0] == point:
            count["n"] += 1
            if count["n"] == nth:
                raise Crash(event)
    return hook


def scripted(n: int = 120) -> list:
    """Crossing orders with client order ids, from three accounts on two symbols."""
    out = []
    for i in range(n):
        account = 1 + i % 3
        side = "buy" if i % 2 else "sell"
        price = 100 + (i * 7) % 5 - 2
        out.append(new(account, "AAA" if i % 4 else "BBB", side, price, 1 + i % 6, f"o{i}"))
    return out


def reference_state(tmp_path: Path, requests: list) -> dict:
    m = open_matcher(tmp_path / "ref", FakeClock())
    run(m, requests, batch=10)
    state = {s: (book(m, s), m.engine(s).positions()) for s in ("AAA", "BBB")}
    m.close()
    return state


@pytest.mark.parametrize("point", CRASH_POINTS)
@pytest.mark.parametrize("loss", ["process", "power_keep_journal", "power_lose_unsynced"])
def test_a_crash_then_retries_with_the_same_keys_execute_every_order_exactly_once(
        tmp_path: Path, point: str, loss: str) -> None:
    requests = scripted()
    expected = reference_state(tmp_path, requests)
    data = tmp_path / "live"
    m = open_matcher(data, FakeClock())
    batches = [requests[i:i + 10] for i in range(0, len(requests), 10)]
    for b in batches[:5]:
        m.process_batch(b)
    m.fault_hook = crash_at(point)
    with pytest.raises(Crash):
        m.process_batch(batches[5])  # no replies went out: the gateway times out, the client retries
    durable = m.durable_sizes()
    m.abandon()
    if loss != "process":
        power_loss(durable, keep_unsynced_journal=loss == "power_keep_journal")

    m = open_matcher(data, FakeClock())
    for b in batches[5:]:
        m.process_batch(b)
    keys = journaled_keys(data, ["AAA", "BBB"])
    assert sorted(keys) == sorted({(r.account_id, r.key) for r in requests})  # each exactly once
    assert {s: (book(m, s), m.engine(s).positions()) for s in ("AAA", "BBB")} == expected
    m.close()


def test_power_loss_cannot_keep_a_journal_record_whose_intake_entry_is_lost(tmp_path: Path) -> None:
    """The bug the write order prevents: the kernel writes a journal page back on its own,
    the intake page is still dirty, the power goes. Then the order exists without its
    client_order_id and a retry would create a duplicate."""
    m = open_matcher(tmp_path, FakeClock())
    m.fault_hook = crash_at("engine", nth=1)  # journaled and matched, journal not yet synced
    with pytest.raises(Crash):
        m.process_batch([new(1, "AAA", "buy", 100, 5, "only-once")])
    durable = m.durable_sizes()
    m.abandon()
    power_loss(durable, keep_unsynced_journal=True)

    m = open_matcher(tmp_path, FakeClock())
    [retry] = m.process_batch([new(1, "AAA", "buy", 100, 5, "only-once")]).replies
    assert m.last_seq_no("AAA") == 1
    assert retry.replayed and retry.order_id == 1


def test_orphan_intake_entries_are_dropped_and_their_ids_reused(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 5), new(1, "AAA", "buy", 99, 5)])
    m.fault_hook = crash_at("intake.sync", nth=1)  # entries written, nothing journaled
    with pytest.raises(Crash):
        m.process_batch([new(1, "AAA", "buy", 98, 5, "x"), new(1, "AAA", "buy", 97, 5, "y")])
    m.abandon()
    assert len(IntakeLog.load(intake_path(tmp_path, "AAA"))) == 4  # two orphans on disk

    m = open_matcher(tmp_path, FakeClock())
    assert [e.seq_no for e in IntakeLog.load(intake_path(tmp_path, "AAA"))] == [1, 2]
    [r] = m.process_batch([new(1, "AAA", "buy", 98, 5, "y")]).replies
    assert (r.replayed, r.order_id, r.seq_no) == (False, 3, 3)


def test_a_torn_journal_tail_and_a_torn_intake_line_are_trimmed_on_restart(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 5), new(1, "AAA", "buy", 99, 5)])
    m.abandon()
    with journal_path(tmp_path, "AAA").open("ab") as f:
        f.write(b"\x3e\x00\x00\x00\x03")
    with intake_path(tmp_path, "AAA").open("ab") as f:
        f.write(b'{"seq_no": 3, "requ')
    m = open_matcher(tmp_path, FakeClock())
    assert m.last_seq_no("AAA") == 2
    [r] = run(m, [new(1, "AAA", "buy", 98, 5)])
    assert (r.seq_no, r.order_id) == (3, 3)


# --- Restart from snapshots ----------------------------------------------------------------------

def engine_state(engine: mf.Engine) -> bytes:
    return mf.encode_snapshot(engine.snapshot(last_timestamp=engine.last_timestamp, journal_offset=0))


def full_replay(journal: Path) -> mf.Engine:
    engine = mf.Engine()
    for rec in mf.JournalReader(journal):
        engine.process(rec.command, rec.timestamp)
    return engine


def test_restart_from_snapshot_and_tail_equals_a_full_replay(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock(), snapshot_every=100)
    run(m, random_flow(3, 1500, symbols=["AAA", "BBB"]))
    m.abandon()  # no snapshot at exit: restart has to replay a tail
    m = open_matcher(tmp_path, FakeClock(), snapshot_every=100)
    for symbol in ("AAA", "BBB"):
        info = m.recovery[symbol]
        assert info.snapshot_seq_no > 0 and info.replayed == m.last_seq_no(symbol) - info.snapshot_seq_no
        assert engine_state(m.engine(symbol)) == engine_state(full_replay(journal_path(tmp_path, symbol)))


def test_a_damaged_newest_snapshot_falls_back_to_an_older_one(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock(), snapshot_every=100)
    run(m, random_flow(4, 800))
    m.abandon()
    snaps = sorted((tmp_path / "AAA" / "snapshots").glob("snap-*.mfsnap"))
    assert len(snaps) >= 2
    snaps[-1].write_bytes(snaps[-1].read_bytes()[:-5])
    m = open_matcher(tmp_path, FakeClock())
    assert m.recovery["AAA"].snapshot_seq_no == int(snaps[-2].stem.removeprefix("snap-"))
    assert engine_state(m.engine("AAA")) == engine_state(full_replay(journal_path(tmp_path, "AAA")))


def test_a_snapshot_beyond_the_journal_is_ignored(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock(), snapshot_every=10)
    run(m, random_flow(5, 40))
    m.abandon()
    # Power loss took the journal tail but a later snapshot file survived.
    journal = journal_path(tmp_path, "AAA")
    reader = mf.JournalReader(journal)
    for _ in range(15):
        next(reader)
    with journal.open("r+b") as f:
        f.truncate(reader.offset)
    m = open_matcher(tmp_path, FakeClock())
    assert m.last_seq_no("AAA") == 15 and m.recovery["AAA"].snapshot_seq_no <= 15
    assert engine_state(m.engine("AAA")) == engine_state(full_replay(journal))


def test_order_ids_and_keys_survive_a_restart(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 5, "k"), new(1, "BBB", "buy", 100, 5)])
    m.close()
    m = open_matcher(tmp_path, FakeClock())
    again, fresh, other = run(m, [new(1, "AAA", "buy", 100, 5, "k"), new(1, "AAA", "buy", 99, 5),
                                  cancel(1, 2)], batch=1)
    assert (again.replayed, again.order_id) == (True, 1)
    assert fresh.order_id == 3
    assert (other.symbol, other.status) == ("BBB", "ok")


# --- Single writer, failure, admin ---------------------------------------------------------------

def test_a_second_matcher_on_the_same_directory_is_refused(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    with pytest.raises(MatcherLocked):
        open_matcher(tmp_path, FakeClock())
    m.close()
    open_matcher(tmp_path, FakeClock()).close()


def test_an_engine_failure_halts_only_that_symbol(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 5)])
    mf._core._inject_fault_after_next_command(m.engine("AAA"))
    failed, halted, other = run(m, [new(1, "AAA", "buy", 99, 5), new(1, "AAA", "buy", 98, 5),
                                    new(1, "BBB", "buy", 98, 5)], batch=3)
    assert (failed.status, failed.code) == ("error", "ENGINE_FAILED")
    assert (halted.status, halted.code) == ("error", "SYMBOL_HALTED")
    assert other.status == "ok"
    assert m.halted("AAA") and not m.halted("BBB")
    assert m.last_seq_no("AAA") == 2  # the failing command was journaled (write-ahead), the next was not


def test_first_start_journals_the_instrument_risk_config(tmp_path: Path) -> None:
    config = tmp_path / "risk.yaml"
    config.write_text("instrument:\n  max_qty: 10\n")
    instruments = [Instrument(symbol="AAA", tick_size="0.01", risk_config=config)]
    m = open_matcher(tmp_path / "data", FakeClock(), instruments)
    k = m.last_seq_no("AAA")
    assert k == len(mf.load_risk_config(config)) > 0
    [r] = run(m, [new(1, "AAA", "buy", 100, 11)])
    assert (r.seq_no, r.reason) == (k + 1, "MAX_QTY")
    m.close()
    m = open_matcher(tmp_path / "data", FakeClock(), instruments)
    assert m.last_seq_no("AAA") == k + 1  # not journaled twice


def test_an_admin_command_without_a_symbol_applies_to_every_symbol(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(2, "AAA", "buy", 100, 5), new(2, "BBB", "buy", 100, 5)])
    [r] = run(m, [admin("kill_switch", engaged=True, target_account=2, cancel_open=True)])
    assert r.status == "ok" and sorted(x["symbol"] for x in r.results) == ["AAA", "BBB"]
    assert all(m.engine(s).order_count == 0 for s in ("AAA", "BBB"))
    [blocked] = run(m, [new(2, "AAA", "buy", 100, 5)])
    assert blocked.reason == "KILL_SWITCH"
    [halt] = run(m, [admin("session", symbol="BBB", state="halted")])
    assert [x["symbol"] for x in halt.results] == ["BBB"]


def test_execution_reports_go_to_the_accounts_involved(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    m.process_batch([new(1, "AAA", "sell", 100, 5)])
    result = m.process_batch([new(2, "AAA", "buy", 100, 8, tif="ioc"), new(3, "AAA", "buy", 100, 0)])
    by_account: dict[int, list[str]] = {}
    for account, report in result.executions:
        by_account.setdefault(account, []).append(report["event"]["type"])
    assert by_account == {1: ["fill"], 2: ["ack", "fill", "cancelled"], 3: ["reject"]}
    [c] = run(m, [new(1, "AAA", "sell", 105, 5), cancel(1, 4)], batch=2)[1:]
    assert c.status == "ok"


def test_deadline_uses_the_matcher_clock(tmp_path: Path) -> None:
    clock = FakeClock()
    m = open_matcher(tmp_path, clock)
    request = new(1, "AAA", "buy", 100, 5, deadline=clock.now + 10)
    clock.advance(11)
    [r] = m.process_batch([request]).replies
    assert r.code == "EXPIRED"
