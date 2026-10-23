"""Market data (M10): the matcher publishes a book snapshot plus numbered L2 deltas per
symbol; the reference client (services/client.py) applies them, detects a gap or a new
matcher epoch, and re-snapshots."""

from __future__ import annotations

from pathlib import Path

import mfengine as mf

from svc_helpers import FakeClock, new, open_matcher, random_flow
from services.client import BookClient

DEPTH = 5  # small, so levels move in and out of the published window


def top(matcher, symbol: str) -> tuple[list, list]:
    e = matcher.engine(symbol)
    return [tuple(x) for x in e.depth(mf.Side.BUY, DEPTH)], [tuple(x) for x in e.depth(mf.Side.SELL, DEPTH)]


def test_snapshot_plus_deltas_reproduce_the_published_depth_after_every_batch(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock(), depth=DEPTH)
    clients = {s: BookClient(s) for s in ("AAA", "BBB")}
    for s, c in clients.items():
        assert c.on_message(m.book_snapshot(s))
    flow = random_flow(11, 4000, symbols=["AAA", "BBB"])
    seen_seq = {"AAA": clients["AAA"].md_seq, "BBB": clients["BBB"].md_seq}
    trades = 0
    for i in range(0, len(flow), 9):
        result = m.process_batch(flow[i:i + 9])
        for msg in result.market_data:
            assert msg["md_seq"] == seen_seq[msg["symbol"]] + 1  # contiguous per symbol
            seen_seq[msg["symbol"]] = msg["md_seq"]
            trades += len(msg["trades"])
            assert clients[msg["symbol"]].on_message(msg)
        for s, c in clients.items():
            assert (c.bids(), c.asks()) == top(m, s)
            assert result.books.get(s, m.book_snapshot(s))["md_seq"] == c.md_seq
    assert trades > 100


def test_trades_in_updates_match_the_trade_events(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    m.process_batch([new(1, "AAA", "sell", 100, 5), new(1, "AAA", "sell", 101, 5)])
    result = m.process_batch([new(2, "AAA", "buy", 101, 7)])
    [msg] = result.market_data
    assert [(t["price"], t["qty"], t["aggressor_side"]) for t in msg["trades"]] == [(100, 5, "buy"), (101, 2, "buy")]
    assert msg["l1"] == {"bid": None, "bid_qty": 0, "ask": 101, "ask_qty": 3}


def test_a_batch_that_changes_nothing_publishes_nothing(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    result = m.process_batch([new(1, "AAA", "buy", 100, 0)])  # rejected
    assert result.market_data == []


def test_a_gap_makes_the_client_resnapshot(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    client = BookClient("AAA")
    client.on_message(m.book_snapshot("AAA"))
    msgs = [m.process_batch([new(1, "AAA", "buy", 100 - i, 1)]).market_data[0] for i in range(3)]
    assert client.on_message(msgs[0])
    assert not client.on_message(msgs[2])  # md_seq skipped one
    assert not client.synced
    assert not client.on_message(msgs[1])  # still unsynced: deltas are ignored until a snapshot
    assert client.on_message(m.book_snapshot("AAA"))
    assert (client.bids(), client.asks()) == top(m, "AAA")


def test_old_deltas_after_a_snapshot_are_ignored(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    first = m.process_batch([new(1, "AAA", "buy", 100, 1)]).market_data[0]
    client = BookClient("AAA")
    client.on_message(m.book_snapshot("AAA"))
    assert client.on_message(first)  # already in the snapshot: skipped, still synced
    assert client.synced and client.bids() == [(100, 1, 1)]


def test_a_new_matcher_epoch_forces_a_resnapshot(tmp_path: Path) -> None:
    m = open_matcher(tmp_path, FakeClock())
    m.epoch = 1
    client = BookClient("AAA")
    client.on_message(m.book_snapshot("AAA"))
    m.process_batch([new(1, "AAA", "buy", 100, 1)])
    m.close()
    m = open_matcher(tmp_path, FakeClock())
    m.epoch = 2
    msg = m.process_batch([new(1, "AAA", "buy", 99, 1)]).market_data[0]
    assert (msg["epoch"], msg["md_seq"]) == (2, 1)
    assert not client.on_message(msg)
    assert client.on_message(m.book_snapshot("AAA"))
    assert client.bids() == [(100, 1, 1), (99, 1, 1)]
