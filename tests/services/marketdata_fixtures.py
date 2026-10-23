"""Market-data message fixtures (M11): the same files drive the Python reference client
(services/client.py, `BookClient`) and the dashboard's (dashboard/src/lib/bookClient.ts),
so the two provably agree.

Every message comes from a real matcher (snapshots and updates as it publishes them),
fed to a client in a scripted order: in sequence, with a gap, stale after a snapshot,
across a matcher restart (new epoch), after a slow-consumer resnapshot, before any
snapshot, and with levels moving in and out of the published window. After each
message the expected client state is recorded; whenever the client is synced with the
newest message, that state is first checked against the engine's own depth, so the
expectations rest on the engine, not on either client.

    .venv/bin/python tests/services/marketdata_fixtures.py   # rewrite tests/fixtures/marketdata/cases.json
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import mfengine as mf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from svc_helpers import FakeClock, cancel, new, open_matcher, random_flow  # noqa: E402

from services.client import BookClient  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "marketdata" / "cases.json"


class Case:
    def __init__(self, name: str, matcher: Any, symbol: str = "AAA", depth: int = 50) -> None:
        self.name, self.matcher, self.symbol, self.levels = name, matcher, symbol, depth
        self.client = BookClient(symbol)
        self.steps: list[dict[str, Any]] = []

    def depth(self) -> tuple[list[list[int]], list[list[int]]]:
        e, d = self.matcher.engine(self.symbol), self.levels
        return [list(x) for x in e.depth(mf.Side.BUY, d)], [list(x) for x in e.depth(mf.Side.SELL, d)]

    def feed(self, msg: dict[str, Any], *, current: bool = False) -> None:
        """Applies msg; `current` = msg is the newest state of the book, so a synced client
        must equal the engine's depth (checked here, before anything is recorded)."""
        result = self.client.on_message(msg)
        state = {"result": result, "synced": self.client.synced, "epoch": self.client.epoch,
                 "md_seq": self.client.md_seq, "resnapshots": self.client.resnapshots,
                 "bids": [list(x) for x in self.client.bids()], "asks": [list(x) for x in self.client.asks()]}
        if current and self.client.synced:
            bids, asks = self.depth()
            assert (state["bids"], state["asks"]) == (bids, asks), f"{self.name}: client differs from the engine"
        self.steps.append({"message": msg, "expect": state})

    def batch(self, requests: list[Any]) -> tuple[list[dict[str, Any]], list[Any]]:
        result = self.matcher.process_batch(requests)
        return [m for m in result.market_data if m["symbol"] == self.symbol], result.replies

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "symbol": self.symbol, "steps": self.steps}


def build(tmp: Path) -> dict[str, Any]:
    cases = []

    # 1. In order: a snapshot, then every update of a random flow.
    m = open_matcher(tmp / "1", FakeClock(), depth=5)
    m.epoch = 1
    c = Case("in order: snapshot then contiguous updates", m, depth=5)
    c.feed(m.book_snapshot("AAA"), current=True)
    flow = random_flow(11, 600, symbols=["AAA"])
    for i in range(0, len(flow), 6):
        msgs, _ = c.batch(flow[i:i + 6])
        for j, msg in enumerate(msgs):
            c.feed(msg, current=j == len(msgs) - 1)
    assert sum(len(s["message"].get("trades", [])) for s in c.steps) > 10
    cases.append(c.to_dict())
    m.close()

    # 2. Updates already in the snapshot are skipped; the next one applies.
    m = open_matcher(tmp / "2", FakeClock())
    m.epoch = 1
    c = Case("stale updates after a snapshot are ignored", m)
    early = [c.batch([new(1, "AAA", "buy", 100 - i, 1 + i)])[0][0] for i in range(3)]
    c.feed(m.book_snapshot("AAA"), current=True)
    c.feed(early[1])
    c.feed(early[2], current=True)
    c.feed(c.batch([new(2, "AAA", "sell", 105, 4)])[0][0], current=True)
    cases.append(c.to_dict())
    m.close()

    # 3. A gap: unsynced until a new snapshot; deltas in between are refused.
    m = open_matcher(tmp / "3", FakeClock())
    m.epoch = 1
    c = Case("a gap in md_seq makes the client unsynced until a snapshot", m)
    c.feed(m.book_snapshot("AAA"), current=True)
    msgs = [c.batch([new(1, "AAA", "buy", 100 - i, 2)])[0][0] for i in range(3)]
    c.feed(msgs[0])
    c.feed(msgs[2])  # skipped md_seq 2
    c.feed(msgs[1])
    c.feed(m.book_snapshot("AAA"), current=True)
    c.feed(c.batch([new(2, "AAA", "sell", 104, 3)])[0][0], current=True)
    cases.append(c.to_dict())
    m.close()

    # 4. A matcher restart: a new epoch restarts md_seq at 1; the old snapshot is no good.
    m = open_matcher(tmp / "4", FakeClock())
    m.epoch = 1
    c = Case("a new matcher epoch forces a resnapshot", m)
    c.feed(m.book_snapshot("AAA"), current=True)
    c.feed(c.batch([new(1, "AAA", "buy", 100, 1)])[0][0], current=True)
    m.close()
    m = open_matcher(tmp / "4", FakeClock())
    m.epoch = 2
    c.matcher = m
    c.feed(c.batch([new(1, "AAA", "buy", 99, 1)])[0][0])
    c.feed(m.book_snapshot("AAA"), current=True)
    c.feed(c.batch([new(2, "AAA", "sell", 102, 5)])[0][0], current=True)
    cases.append(c.to_dict())
    m.close()

    # 5. The gateway's slow-consumer path: "resnapshot", then a fresh snapshot.
    m = open_matcher(tmp / "5", FakeClock())
    m.epoch = 1
    c = Case("a resnapshot message, then a fresh snapshot", m)
    c.feed(m.book_snapshot("AAA"), current=True)
    c.feed(c.batch([new(1, "AAA", "buy", 100, 3)])[0][0], current=True)
    c.feed({"type": "resnapshot", "symbol": "AAA"})
    missed = c.batch([new(2, "AAA", "sell", 101, 2)])[0][0]
    c.feed(c.batch([new(2, "AAA", "sell", 102, 2)])[0][0])  # dropped by the gateway: unsynced
    c.feed(m.book_snapshot("AAA"), current=True)
    c.feed(missed)  # older than the snapshot
    cases.append(c.to_dict())
    m.close()

    # 6. Nothing applies before the first snapshot; other message types change nothing.
    m = open_matcher(tmp / "6", FakeClock())
    m.epoch = 1
    c = Case("updates before any snapshot, and other message types", m)
    c.feed(c.batch([new(1, "AAA", "buy", 100, 1)])[0][0])
    c.feed({"type": "pong", "id": 1})
    c.feed(m.book_snapshot("AAA"), current=True)
    c.feed({"type": "pong", "id": 2})
    c.feed({"type": "something_new", "x": 1})
    cases.append(c.to_dict())
    m.close()

    # 7. A 3-level window: cancelling the best levels brings deeper ones into view, and a
    # sweep removes several levels and prints trades in one update.
    m = open_matcher(tmp / "7", FakeClock(), depth=3)
    m.epoch = 1
    c = Case("levels move in and out of the published window", m, depth=3)
    _, replies = c.batch([new(1, "AAA", "buy", 100 - i, 10 + i) for i in range(6)])
    c.batch([new(1, "AAA", "sell", 110 + i, 5) for i in range(5)])
    c.feed(m.book_snapshot("AAA"), current=True)  # 3 of 6 bid levels and 3 of 5 ask levels
    for r in replies[:2]:
        c.feed(c.batch([cancel(1, r.order_id)])[0][0], current=True)
    c.feed(c.batch([new(2, "AAA", "sell", 95, 40, tif="ioc")])[0][0], current=True)
    c.feed(c.batch([new(3, "AAA", "buy", 112, 12, tif="ioc")])[0][0], current=True)
    cases.append(c.to_dict())
    m.close()

    return {"_note": "Generated by tests/services/marketdata_fixtures.py from a real matcher (M11). Each step: a "
                     "message as the gateway sends it, and the client state expected after it. Do not edit by hand.",
            "cases": cases}


def render() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        return json.dumps(build(Path(tmp)), indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render())
    print(f"wrote {OUT}")
