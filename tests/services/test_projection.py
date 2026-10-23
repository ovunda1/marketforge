"""The Postgres projection (M10), without Postgres: the projector replays each symbol's
journal through its own engine and turns records, events and intake entries into row
operations. The in-memory store applies them under the same cursor rule as the real
one, so incremental projection, rebuilds and idempotency are tested here."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from svc_helpers import FakeClock, admin, cancel, journal_path, new, open_matcher, random_flow, replace, run
from services.projector.projection import CursorMismatch, MemoryStore, Projection
from services.projector.source import SymbolSource


def project_all(data_dir: Path, symbols=("AAA",), chunk=None, seed: int = 0) -> MemoryStore:
    store = MemoryStore()
    rng = random.Random(seed)
    for symbol in symbols:
        source = SymbolSource(data_dir, symbol)
        source.start(store.cursor(symbol))
        while True:
            batch = source.poll(rng.randint(1, chunk) if chunk else 10**9)
            if not batch:
                break
            store.apply(symbol, Projection.ops(symbol, batch), expected=store.cursor(symbol),
                        to=batch[-1].seq_no)
    return store


def test_a_hand_derived_session(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [
        new(1, "AAA", "sell", 100, 10, "s1"),            # order 1 rests
        new(2, "AAA", "buy", 101, 4, "b1"),              # order 2: fills 4 @ 100
        new(2, "AAA", "buy", 100, 10, "b2", tif="ioc"),  # order 3: fills 6 @ 100, 4 cancelled
        new(3, "AAA", "sell", 105, 2, "s2"),             # order 4 rests
        replace(3, 4, 106, 3),
        new(3, "AAA", "buy", 106, 1, "p1", post_only=True),  # order 5: rejected
        cancel(3, 4),
    ], batch=1)
    m.close()
    s = project_all(tmp_path)
    o = {r["order_id"]: r for r in s.rows("orders")}
    assert {k: (r["status"], r["cum_qty"], r["leaves_qty"], r["client_order_id"]) for k, r in o.items()} == {
        1: ("filled", 10, 0, "s1"), 2: ("filled", 4, 0, "b1"), 3: ("cancelled", 6, 0, "b2"),
        4: ("cancelled", 0, 0, "s2"), 5: ("rejected", 0, 0, "p1")}
    assert o[4]["price"] == 106 and o[4]["qty"] == 3
    assert o[5]["reject_reason"] == "POST_ONLY_WOULD_CROSS"
    assert [(t["trade_id"], t["price"], t["qty"]) for t in s.rows("trades")] == [(1, 100, 4), (2, 100, 6)]
    assert sorted((f["order_id"], f["qty"], f["liquidity"]) for f in s.rows("fills")) == [
        (1, 4, "maker"), (1, 6, "maker"), (2, 4, "taker"), (3, 6, "taker")]
    pos = {(p["account_id"], p["symbol"]): (p["qty"], p["cash"]) for p in s.rows("positions")}
    assert pos == {(1, "AAA"): (-10, 1000), (2, "AAA"): (10, -1000)}
    assert s.cursor("AAA") == 7


def test_admin_commands_project_limits_and_sessions(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [admin("instrument_limits", symbol="AAA", max_qty=50),
            admin("kill_switch", symbol="AAA", engaged=True, target_account=4),
            admin("session", symbol="AAA", state="halted"),
            admin("session", symbol="AAA", state="pre_open")])  # HALTED -> PRE_OPEN is invalid: no change
    m.close()
    s = project_all(tmp_path)
    limits = {(r["scope"], r["account_id"]): r["limits"] for r in s.rows("risk_limits")}
    assert limits[("instrument", 0)]["max_qty"] == 50
    assert limits[("kill_switch", 4)] == {"engaged": True}
    assert [(r["symbol"], r["state"]) for r in s.rows("sessions")] == [("AAA", "halted")]


def test_incremental_projection_in_random_chunks_equals_a_one_shot_rebuild(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock(), snapshot_every=97)
    run(m, random_flow(21, 3000, symbols=["AAA", "BBB"]))
    m.close()
    once = project_all(tmp_path, ("AAA", "BBB"))
    for seed in range(3):
        assert project_all(tmp_path, ("AAA", "BBB"), chunk=50, seed=seed).dump() == once.dump()


def test_applying_a_batch_with_a_stale_cursor_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "sell", 100, 10), new(2, "AAA", "buy", 100, 4)])
    m.close()
    source = SymbolSource(tmp_path, "AAA")
    source.start(0)
    batch = source.poll(10)
    store = MemoryStore()
    store.apply("AAA", Projection.ops("AAA", batch), expected=0, to=2)
    before = store.dump()
    with pytest.raises(CursorMismatch):  # a second projector, or a replayed batch
        store.apply("AAA", Projection.ops("AAA", batch), expected=0, to=2)
    assert store.dump() == before


def test_starting_from_a_cursor_uses_a_snapshot_and_continues_exactly(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock(), snapshot_every=50)
    run(m, random_flow(22, 600))
    m.close()
    full = SymbolSource(tmp_path, "AAA")
    full.start(0)
    records = full.poll(10**9)
    cursor = len(records) - 20  # the matcher keeps its newest snapshots only
    later = SymbolSource(tmp_path, "AAA")
    later.start(cursor)
    assert 0 < later.started_from_snapshot <= cursor
    assert later.poll(10**9) == records[cursor:]


def test_the_source_stops_at_a_torn_tail_and_resumes_when_it_is_complete(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 1), new(1, "AAA", "buy", 99, 1)])
    m.close()
    journal = journal_path(tmp_path, "AAA")
    data = journal.read_bytes()
    journal.write_bytes(data[:-10])  # the matcher is in the middle of an append
    source = SymbolSource(tmp_path, "AAA")
    source.start(0)
    assert [r.seq_no for r in source.poll(10)] == [1]
    assert source.poll(10) == []
    journal.write_bytes(data)
    assert [r.seq_no for r in source.poll(10)] == [2]


def test_a_cursor_ahead_of_the_journal_means_rebuild(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(1, "AAA", "buy", 100, 1)])
    m.close()
    source = SymbolSource(tmp_path, "AAA")
    assert not source.needs_rebuild(1)
    assert source.needs_rebuild(2)  # e.g. an OS crash took journal records the projection had seen


def test_projected_records_carry_the_client_order_id_from_the_intake_log(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    run(m, [new(5, "AAA", "buy", 100, 1, "mine")])
    m.close()
    source = SymbolSource(tmp_path, "AAA")
    source.start(0)
    [rec] = source.poll(10)
    assert (rec.intake.key, rec.intake.account_id, rec.intake.order_id) == ("mine", 5, 1)
