"""Gateway smoke check (M10, `make gateway-smoke`): N crossing orders through REST on a
running stack, with a WebSocket market-data client attached. Prints one JSON line:
REST round-trip percentiles, and the time from sending an order to receiving the
market-data update that carries its seq_no over WebSocket (often shorter than the REST
round trip: the matcher publishes both at once). A sanity check of the service path, not a benchmark: it runs over
loopback, through Docker's port forwarding, on whatever else the machine is doing.

It trades the dedicated SMOKE symbol (never the demo market) and, at the end, cancels
every order of its two accounts that is still open, so it leaves no resting orders
behind (M11). Its fills stay: they are journaled history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import time
import uuid
from typing import Any


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile of an ascending list (q in [0, 100])."""
    if not sorted_values:
        raise ValueError("no values")
    rank = max(1, min(len(sorted_values), int(-(-q * len(sorted_values) // 100))))
    return sorted_values[rank - 1]


def summary(values_s: list[float]) -> dict[str, float]:
    v = sorted(x * 1e3 for x in values_s)
    return {"n": len(v), "p50_ms": round(percentile(v, 50), 3), "p90_ms": round(percentile(v, 90), 3),
            "p99_ms": round(percentile(v, 99), 3), "max_ms": round(v[-1], 3)}


async def run(url: str, keys: tuple[str, str], n: int, symbol: str, concurrency: int) -> dict[str, Any]:
    import httpx
    import websockets

    seen: dict[int, float] = {}

    async def watch(ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "update":
                seen.setdefault(msg["seq_no"], time.perf_counter())

    ws_url = url.replace("http", "ws", 1) + f"/ws/marketdata/{symbol}"
    sem = asyncio.Semaphore(concurrency)
    round_trips: list[float] = []
    sent_at: dict[int, float] = {}
    async with websockets.connect(ws_url) as ws, httpx.AsyncClient(base_url=url, timeout=30) as client:
        watcher = asyncio.create_task(watch(ws))

        async def one(i: int) -> None:
            body = {"symbol": symbol, "side": "buy" if i % 2 else "sell", "type": "limit", "tif": "gtc",
                    "price": 10_000 + (i % 5) - 2, "qty": 1, "client_order_id": uuid.uuid4().hex}
            async with sem:
                start = time.perf_counter()
                r = await client.post("/orders", json=body, headers={"X-API-Key": keys[i % 2]})
                end = time.perf_counter()
            if r.status_code in (200, 201, 422):
                round_trips.append(end - start)
                seq = r.json().get("seq_no") or r.json().get("error", {}).get("seq_no")
                if seq:
                    sent_at[seq] = start

        started = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(n)))
        elapsed = time.perf_counter() - started
        await asyncio.sleep(0.5)
        watcher.cancel()
        last_seq = max(sent_at, default=0)
        cleanup = await clean_up(client, keys, symbol, last_seq)
    lags = [seen[s] - t for s, t in sent_at.items() if s in seen]
    return {"orders": n, "ok": len(round_trips), "concurrency": concurrency, "orders_per_s": round(n / elapsed, 1),
            "rest_round_trip": summary(round_trips), "send_to_md_update": summary(lags) if lags else None,
            "cleanup": cleanup, "machine": {"system": platform.system(), "machine": platform.machine()}}


async def clean_up(client: Any, keys: tuple[str, str], symbol: str, seq_no: int) -> dict[str, int]:
    """Cancels every order of both accounts still open on `symbol` (read once the
    projection has caught up with the run), until none is left."""
    cancelled = 0
    for key in keys:
        headers = {"X-API-Key": key}
        for _ in range(20):
            r = await client.get("/orders", params={"status": "open", "symbol": symbol, "min_seq_no": seq_no,
                                                    "limit": 500}, headers=headers)
            r.raise_for_status()
            open_orders = r.json()["orders"]
            if not open_orders:
                break
            for o in open_orders:
                d = await client.delete(f"/orders/{o['order_id']}", headers=headers)
                if d.status_code == 200:
                    cancelled += 1
                    seq_no = max(seq_no, d.json().get("seq_no") or 0)
    left = 0
    for key in keys:
        r = await client.get("/orders", params={"status": "open", "symbol": symbol, "min_seq_no": seq_no},
                             headers={"X-API-Key": key})
        left += len(r.json()["orders"])
    return {"cancelled": cancelled, "open_after": left}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.smoke")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--key", required=True)
    parser.add_argument("--key2", required=True)
    parser.add_argument("--symbol", default="SMOKE")
    parser.add_argument("--n", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=32)
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(args.url, (args.key, args.key2), args.n, args.symbol, args.concurrency))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
