"""Market data over WebSocket on the compose stack (M10): a snapshot, then numbered
deltas that rebuild exactly the book that GET /book serves."""

from __future__ import annotations

import json

import websockets.sync.client as wsc

from services.client import BookClient
from .stack import WS, Account, client, eventually, order


def test_snapshot_plus_deltas_equal_the_rest_book(alice: Account, bob: Account) -> None:
    ws = wsc.connect(f"{WS}/ws/marketdata/HHH")
    feed = BookClient("HHH")
    first = json.loads(ws.recv(timeout=10))
    assert first["type"] == "snapshot" and feed.on_message(first)
    trades = 0
    with client() as c:
        for i in range(200):
            who = (alice, bob)[i % 2]
            c.post("/orders", json=order("HHH", "buy" if i % 2 else "sell", 9000 + i % 11 - 5, 1 + i % 4),
                   headers=who.headers)
        final = c.get("/book/HHH?depth=50").json()

    def caught_up() -> bool:
        nonlocal trades
        while feed.md_seq < final["md_seq"]:
            msg = json.loads(ws.recv(timeout=10))
            assert feed.on_message(msg), msg  # no gap on a healthy connection
            trades += len(msg.get("trades", []))
        return True
    eventually(caught_up)
    assert feed.md_seq == final["md_seq"]
    assert [[list(x) for x in feed.bids()], [list(x) for x in feed.asks()]] == [final["bids"], final["asks"]]
    assert trades > 0
    ws.close()


def test_an_unknown_symbol_is_refused() -> None:
    try:
        with wsc.connect(f"{WS}/ws/marketdata/NOPE") as ws:
            ws.recv(timeout=5)
        raise AssertionError("expected a close")
    except Exception as e:  # noqa: BLE001
        assert "4404" in str(e), e
