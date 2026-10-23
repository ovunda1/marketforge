"""Crashes and slow consumers on the compose stack (M10): kill -9 the matcher, the
projector and the backtest worker under load; stall a WebSocket client."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import websockets.sync.client as wsc

from services.client import BookClient
from .stack import (WS, Account, client, compose, create_account, eventually, order, wait_projected)


def book(symbol: str) -> dict:
    with client() as c:
        return c.get(f"/book/{symbol}?depth=50").json()


def slow_consumer_actions() -> float:
    """Slow-consumer closes plus resnapshots, summed over the API workers (/metrics)."""
    with client() as c:
        text = c.get("/metrics").text
    return sum(float(line.rsplit(" ", 1)[1]) for line in text.splitlines()
               if line.startswith(("mf_ws_slow_consumers_total", "mf_ws_resnapshots_total")))


def send_until_answered(c: httpx.Client, body: dict, account: Account) -> httpx.Response:
    """A client that retries with the same client_order_id until it gets an answer."""
    while True:
        try:
            r = c.post("/orders", json=body, headers=account.headers)
            if r.status_code not in (502, 503, 504):
                return r
        except httpx.TransportError:
            pass
        time.sleep(0.2)


def test_kill_the_matcher_under_load_then_retry_with_the_same_keys() -> None:
    accounts = [create_account() for _ in range(4)]
    bodies = [(accounts[i % 4], order("EEE", "buy" if i % 2 else "sell", 6000 + i % 5 - 2, 1 + i % 3))
              for i in range(400)]
    ws = wsc.connect(f"{WS}/ws/marketdata/EEE")
    feed = BookClient("EEE")
    assert feed.on_message(json.loads(ws.recv(timeout=10)))
    old_epoch = feed.epoch

    def killer() -> None:
        time.sleep(0.5)
        compose("kill", "-s", "SIGKILL", "matcher")
        with client() as c:
            eventually(lambda: c.get("/health").json()["matcher"] == "down", what="health to see the matcher down")
        compose("start", "matcher")
    k = threading.Thread(target=killer)
    k.start()

    def send(item: tuple[Account, dict]) -> int:
        with client() as c:
            r = send_until_answered(c, item[1], item[0])
            assert r.status_code in (200, 201, 422), r.text
            return r.json().get("order_id") or r.json()["error"]["order_id"]
    with ThreadPoolExecutor(16) as pool:
        ids = list(pool.map(send, bodies))
    k.join()
    # Every retry after the restart resolves to one order: send all of them once more.
    with ThreadPoolExecutor(16) as pool:
        assert list(pool.map(send, bodies)) == ids
    assert len(set(ids)) == len(bodies)

    report = wait_projected()
    assert report["symbols"]["EEE"]["orders"] >= len(bodies)
    snap = book("EEE")
    assert [snap["bids"], snap["asks"]] == report["symbols"]["EEE"]["book"]

    def resynced() -> bool:
        while True:
            try:
                msg = json.loads(ws.recv(timeout=0.5))
            except TimeoutError:
                break
            feed.on_message(msg)
        return feed.synced and feed.epoch > old_epoch and feed.md_seq == book("EEE")["md_seq"]
    eventually(resynced, what="the WebSocket client to resnapshot on the new epoch")
    assert [[list(x) for x in feed.bids()], [list(x) for x in feed.asks()]] == [snap["bids"], snap["asks"]]
    ws.close()


def test_kill_the_projector_mid_stream_and_it_catches_up_without_duplicates() -> None:
    a, b = create_account(), create_account()

    def send(i: int) -> None:
        with client() as c:
            c.post("/orders", json=order("FFF", "buy" if i % 2 else "sell", 2000, 1), headers=(a, b)[i % 2].headers)
    with ThreadPoolExecutor(16) as pool:
        futures = [pool.submit(send, i) for i in range(300)]
        time.sleep(0.3)
        compose("kill", "-s", "SIGKILL", "projector")
        for f in futures:
            f.result()
    compose("start", "projector")
    report = wait_projected()  # verify compares every row with a fresh replay: duplicates would differ
    assert report["symbols"]["FFF"]["trades"] > 0


def test_a_stalled_websocket_client_is_resnapshotted_or_dropped_while_orders_keep_flowing() -> None:
    a, b = create_account(), create_account()
    fast = wsc.connect(f"{WS}/ws/marketdata/GGG")
    fast_feed = BookClient("GGG")
    fast_feed.on_message(json.loads(fast.recv(timeout=10)))
    # --no-deps: compose must not recreate the api (its dependency) under the test.
    run = compose("run", "-d", "--no-deps", "tester", "python", "-m", "services.client", "slow",
                  "--url", "ws://api:8000/ws/marketdata/GGG", "--run-s", "15")
    container = run.stdout.strip().splitlines()[-1]

    def logs() -> str:
        return subprocess.run(["docker", "logs", container], capture_output=True, text=True).stdout
    eventually(lambda: "connected" in logs(), what="the slow client to connect")
    acted_before = slow_consumer_actions()
    subprocess.run(["docker", "pause", container], check=True)  # it stops reading, mid-stream
    paused = time.monotonic()

    def send(i: int) -> int:
        with client() as c:
            return c.post("/orders", json=order("GGG", "buy" if i % 2 else "sell", 1000 + i % 9 - 4, 1 + i % 5),
                          headers=(a, b)[i % 2].headers).status_code
    with ThreadPoolExecutor(16) as pool:
        statuses = list(pool.map(send, range(4000)))
    elapsed = time.monotonic() - paused
    assert set(statuses) <= {201, 422}  # orders kept completing with the client frozen
    time.sleep(max(0.0, 6.0 - elapsed))  # frozen longer than the gateway's send timeout
    subprocess.run(["docker", "unpause", container], check=True)
    subprocess.run(["docker", "wait", container], capture_output=True, timeout=120)
    out = logs()
    subprocess.run(["docker", "rm", container], capture_output=True)
    result = json.loads(out.strip().splitlines()[-1])
    # The gateway acted on it: resent a snapshot or closed it as a slow consumer. Which
    # close the client sees first depends on timing: our 4008 (queued behind the backlog)
    # or uvicorn's keepalive timeout (1011) if it stayed frozen past the ping timeout.
    assert slow_consumer_actions() > acted_before, result
    assert result["resnapshots"] > 0 or result["closed_code"] in (4008, 1011), result
    assert result["synced_after"] and result["matches_book"], result

    # The fast client saw every update in order, with no gap and no resnapshot.
    gaps = 0
    while True:
        try:
            msg = json.loads(fast.recv(timeout=1))
        except TimeoutError:
            break
        if not fast_feed.on_message(msg):
            gaps += 1
    assert gaps == 0 and fast_feed.synced and fast_feed.resnapshots == 0
    assert fast_feed.md_seq == book("GGG")["md_seq"]
    fast.close()
    print(f"4000 orders in {elapsed:.1f}s with a frozen client attached: {result}")


def test_kill_the_backtest_worker_mid_job_and_the_job_is_retried_to_completion(alice: Account) -> None:
    # A few seconds of work (75 min of synthetic flow, ~5 s on a laptop, more on CI): long
    # enough to be killed mid-run, well inside the test stack's 60 s backtest timeout. The
    # retry starts once arq's lock expires (timeout + 70 s), hence the long wait below.
    config = {"kind": "event", "seed": 3, "data": {"synthetic": {"seed": 21, "duration_ns": 4_500_000_000_000}},
              "broker": {"latency_ns": 1_000_000, "jitter_ns": 200_000},
              "strategy": {"name": "obmom", "params": {"horizon_ns": 200_000_000, "threshold": 2.0, "qty": 2,
                                                       "max_position": 6}}}
    with client() as c:
        run_id = c.post("/backtests", json={"config": config}, headers=alice.headers).json()["id"]
        eventually(lambda: c.get(f"/backtests/{run_id}", headers=alice.headers).json()["status"] == "running",
                   every=0.05, what="the backtest to start")
        compose("kill", "-s", "SIGKILL", "worker")
        compose("start", "worker")
        done = eventually(lambda: (lambda r: r if r["status"] in ("done", "failed") else None)(
            c.get(f"/backtests/{run_id}", headers=alice.headers).json()), timeout=360, what="the retried backtest")
        assert done["status"] == "done", done
        assert done["attempts"] >= 2 and done["metrics"]["kind"] == "event"
        sheet = c.get(done["tearsheet_url"], headers=alice.headers)
        assert sheet.status_code == 200 and sheet.text.lstrip().lower().startswith("<!doctype html>")


def test_a_backtest_streams_progress_over_websocket(alice: Account) -> None:
    config = {"kind": "bars", "seed": 0,
              "data": {"synthetic_bars": {"seed": 4, "symbols": ["A", "B", "C"], "n_days": 400,
                                          "start": "2015-01-02"}},
              "execution": {"capital": 1_000_000_000_000, "lag": 1, "price": "open"},
              "strategy": {"name": "xsmom", "params": {"lookback_months": 6, "skip_months": 1, "quantile": 0.4}}}
    with client() as c:
        run_id = c.post("/backtests", json={"config": config}, headers=alice.headers).json()["id"]
    with wsc.connect(f"{WS}/ws/backtests/{run_id}", additional_headers=alice.headers) as ws:
        msgs = []
        while not msgs or msgs[-1]["type"] not in ("done", "failed"):
            msgs.append(json.loads(ws.recv(timeout=120)))
    assert msgs[-1]["type"] == "done", msgs[-1]
    assert msgs[0]["type"] == "status"
    points = [p for m in msgs if m["type"] == "equity" for p in m["points"]]
    assert len(points) > 100
