"""Reference market-data client (M10), used by the tests and later the dashboard.

`BookClient` applies the matcher's snapshot and numbered updates (services/matcher/
marketdata.py). It is synced after a snapshot; an update with the next md_seq in the same
epoch keeps it synced; an older one is ignored; anything else (a gap, a new epoch) makes
it unsynced until the next snapshot, and on_message returns False so the caller asks for
one. The server sends a fresh snapshot by itself after a slow-consumer overflow
(preceded by a ``resnapshot`` message).

``python -m services.client slow --url ws://.../ws/marketdata/SYM --run-s N`` is the slow
consumer of the compose tests: it connects with small socket buffers and reads, the test
freezes its container for a while (docker pause), and once it has been idle it prints a
JSON report as its last line.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse

Level = tuple[int, int, int]


class BookClient:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.epoch: int | None = None
        self.md_seq = -1
        self.synced = False
        self.resnapshots = 0
        self._bids: dict[int, Level] = {}
        self._asks: dict[int, Level] = {}

    def on_message(self, msg: dict[str, Any]) -> bool:
        """Applies one message; False means "unsynced, a snapshot is needed"."""
        kind = msg.get("type")
        if kind == "snapshot":
            if self.epoch is not None:
                self.resnapshots += 1
            self.epoch, self.md_seq = msg["epoch"], msg["md_seq"]
            self._bids = {lv[0]: tuple(lv) for lv in msg["bids"]}  # type: ignore[misc]
            self._asks = {lv[0]: tuple(lv) for lv in msg["asks"]}  # type: ignore[misc]
            self.synced = True
            return True
        if kind == "resnapshot":
            self.synced = False
            return False
        if kind != "update":
            return self.synced
        if not self.synced:
            return False
        if msg["epoch"] == self.epoch and msg["md_seq"] <= self.md_seq:
            return True  # already in the snapshot
        if msg["epoch"] != self.epoch or msg["md_seq"] != self.md_seq + 1:
            self.synced = False
            return False
        for side, levels in ((self._bids, msg["bids"]), (self._asks, msg["asks"])):
            for price, qty, count in levels:
                if qty == 0:
                    side.pop(price, None)
                else:
                    side[price] = (price, qty, count)
        self.md_seq = msg["md_seq"]
        return True

    def bids(self) -> list[Level]:
        return sorted(self._bids.values(), key=lambda x: -x[0])

    def asks(self) -> list[Level]:
        return sorted(self._asks.values(), key=lambda x: x[0])


def _slow(url: str, run_s: float, idle_s: float) -> dict[str, Any]:
    """Connects with small socket buffers and reads for `run_s` seconds, then until idle.
    The test freezes this process (docker pause) in between: a client that stops reading."""
    import httpx
    from websockets.exceptions import ConnectionClosed
    import websockets.sync.client as wsc

    parsed = urlparse(url)
    symbol = parsed.path.rsplit("/", 1)[-1]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.connect((parsed.hostname, parsed.port or 80))
    feed = BookClient(symbol)
    report: dict[str, Any] = {"messages": 0, "closed_code": None}
    ws = wsc.connect(url, sock=sock, max_queue=1)
    feed.on_message(json.loads(ws.recv(timeout=10)))
    print("connected", flush=True)
    start = last = tick = time.monotonic()
    try:
        while True:
            try:
                msg = json.loads(ws.recv(timeout=0.5))
            except TimeoutError:
                now = time.monotonic()
                if now - tick > 2.0:  # this process was frozen: idle time starts again now
                    last = now
                tick = now
                if now - start > run_s and now - last > idle_s:
                    break
                continue
            last = tick = time.monotonic()
            report["messages"] += 1
            feed.on_message(msg)
    except ConnectionClosed as e:
        report["closed_code"] = e.rcvd.code if e.rcvd else None
    ws.close()
    report["resnapshots"] = feed.resnapshots
    if report["closed_code"] is not None:  # dropped: reconnect and take a snapshot
        with wsc.connect(url) as again:
            feed = BookClient(symbol)
            feed.on_message(json.loads(again.recv(timeout=10)))
    http = f"http://{parsed.hostname}:{parsed.port or 80}"
    book = httpx.get(f"{http}/book/{symbol}?depth=50").json()
    report["synced_after"] = feed.synced
    report["matches_book"] = feed.md_seq == book["md_seq"] and \
        [[list(x) for x in feed.bids()], [list(x) for x in feed.asks()]] == [book["bids"], book["asks"]]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.client")
    sub = parser.add_subparsers(dest="cmd", required=True)
    slow = sub.add_parser("slow", help="a consumer the compose tests freeze (docker pause)")
    slow.add_argument("--url", required=True)
    slow.add_argument("--run-s", type=float, default=20.0)
    slow.add_argument("--idle-s", type=float, default=3.0)
    args = parser.parse_args(argv)
    print(json.dumps(_slow(args.url, args.run_s, args.idle_s)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
