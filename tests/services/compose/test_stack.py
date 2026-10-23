"""The gateway end to end on the compose stack (M10): REST, risk over HTTP, idempotency,
the multi-worker single-writer path, executions, keys and secrets."""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import websockets.sync.client as wsc

from .stack import WS, Account, client, compose, create_account, order, verify_projection, wait_projected


def test_health_metrics_and_openapi() -> None:
    with client() as c:
        health = c.get("/health").json()
        assert health["status"] == "ok" and health["matcher"] == "up", health
        assert "mf_http_requests_total" in c.get("/metrics").text
        assert "/orders" in c.get("/openapi.json").json()["paths"]


def test_an_order_lifecycle(alice: Account, bob: Account) -> None:
    with client() as c:
        sell = c.post("/orders", json=order("AAA", "sell", 5000, 10), headers=alice.headers)
        assert sell.status_code == 201, sell.text
        oid = sell.json()["order_id"]
        assert sell.json()["status"] == "open"
        r = c.patch(f"/orders/{oid}", json={"price": 5001, "qty": 8}, headers=alice.headers)
        assert r.status_code == 200 and r.json()["status"] == "open", r.text
        buy = c.post("/orders", json=order("AAA", "buy", 5001, 3), headers=bob.headers)
        assert buy.json()["status"] == "filled" and buy.json()["fills"][0]["price"] == 5001
        seq = buy.json()["seq_no"]

        got = c.get(f"/orders/{oid}?min_seq_no={seq}", headers=alice.headers).json()
        assert (got["status"], got["cum_qty"], got["leaves_qty"]) == ("partially_filled", 3, 5)
        assert [f["qty"] for f in got["fills"]] == [3]
        assert c.get(f"/orders/{oid}", headers=bob.headers).status_code == 404

        pos = {p["symbol"]: p for p in c.get(f"/accounts/{bob.account_id}/positions?min_seq_no={seq}",
                                             headers=bob.headers).json()["positions"]}
        assert (pos["AAA"]["qty"], pos["AAA"]["cash"]) == (3, -15003)
        trades = c.get("/trades/AAA?limit=5", headers=bob.headers).json()["trades"]
        assert any(t["seq_no"] == seq and t["price"] == 5001 for t in trades)
        asks = c.get("/book/AAA?depth=50", headers=bob.headers).json()["asks"]
        assert [5001, 5] in [a[:2] for a in asks]

        done = c.delete(f"/orders/{oid}", headers=alice.headers)
        assert done.status_code == 200 and done.json()["status"] == "cancelled", done.text
        again = c.delete(f"/orders/{oid}", headers=alice.headers)
        assert again.status_code == 409 and again.json()["error"]["reason"] == "UNKNOWN_ORDER"


def test_risk_rejects_and_admin_controls_over_http(admin: Account, alice: Account) -> None:
    with client() as c:
        r = c.post("/orders", json=order("RISK", "buy", 100, 1001), headers=alice.headers)  # config max_qty 1000
        assert r.status_code == 422 and r.json()["error"]["reason"] == "MAX_QTY"

        lim = c.post("/admin/risk-limits", headers=admin.headers,
                     json={"symbol": "RISK", "scope": "account", "account_id": alice.account_id,
                           "limits": {"max_open_orders": 1}})
        assert lim.status_code == 200, lim.text
        assert c.post("/orders", json=order("RISK", "buy", 100, 1), headers=alice.headers).status_code == 201
        r = c.post("/orders", json=order("RISK", "buy", 99, 1), headers=alice.headers)
        assert r.json()["error"]["reason"] == "MAX_OPEN_ORDERS"

        assert c.post("/admin/kill-switch", headers=alice.headers, json={"engaged": True}).status_code == 403
        ks = c.post("/admin/kill-switch", headers=admin.headers,
                    json={"engaged": True, "account_id": alice.account_id, "cancel_open": True})
        assert ks.status_code == 200 and {x["symbol"] for x in ks.json()["results"]} >= {"AAA", "RISK"}
        r = c.post("/orders", json=order("AAA", "buy", 100, 1), headers=alice.headers)
        assert r.json()["error"]["reason"] == "KILL_SWITCH"

        assert c.post("/admin/session", headers=admin.headers,
                      json={"symbol": "HALT", "state": "halted"}).status_code == 200
        r = c.post("/orders", json=order("HALT", "buy", 100, 1), headers=create_account().headers)
        assert r.json()["error"]["reason"] == "MARKET_CLOSED"
        back = c.post("/admin/session", headers=admin.headers, json={"symbol": "HALT", "state": "continuous"})
        assert back.status_code == 200
        bad = c.post("/admin/session", headers=admin.headers, json={"symbol": "HALT", "state": "pre_open"})
        assert bad.status_code == 422 and bad.json()["error"]["reason"] == "INVALID_TRANSITION"


def test_idempotent_retries_and_concurrent_duplicates(alice: Account) -> None:
    body = order("BBB", "buy", 4000, 2)
    with client() as c:
        first = c.post("/orders", json=body, headers=alice.headers)
        again = c.post("/orders", json=body, headers=alice.headers)
        assert first.status_code == 201 and again.status_code == 200
        assert again.headers["Idempotent-Replayed"] == "true"
        assert again.json()["order_id"] == first.json()["order_id"]
        conflict = c.post("/orders", json=body | {"price": 3999}, headers=alice.headers)
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    dup = order("BBB", "buy", 3990, 1)

    def send(_: int) -> int:
        with client() as c:
            return c.post("/orders", json=dup, headers=alice.headers).json()["order_id"]
    with ThreadPoolExecutor(20) as pool:
        assert len(set(pool.map(send, range(20)))) == 1


def test_many_clients_through_both_api_workers_share_one_gapless_journal() -> None:
    accounts = [create_account() for _ in range(5)]

    def send(i: int) -> tuple[int, str, int | None]:
        a = accounts[i % 5]
        side = "buy" if i % 2 else "sell"
        with client() as c:
            r = c.post("/orders", json=order("CCC", side, 7000 + (i % 7) - 3, 1 + i % 4), headers=a.headers)
            return r.status_code, r.headers.get("X-Worker", ""), r.json().get("order_id")
    with ThreadPoolExecutor(50) as pool:
        results = list(pool.map(send, range(500)))
    assert {s for s, _, _ in results} <= {201, 422}
    assert len({w for _, w, _ in results}) >= 2  # served by more than one API worker
    ids = [o for _, _, o in results]
    assert len(set(ids)) == 500
    report = wait_projected()
    assert report["symbols"]["CCC"]["gapless"]


def test_execution_reports_reach_only_their_account(alice: Account, bob: Account) -> None:
    with wsc.connect(f"{WS}/ws/executions", additional_headers=alice.headers) as ws:
        assert json.loads(ws.recv(timeout=10))["type"] == "subscribed"
        with client() as c:
            mine = c.post("/orders", json=order("DDD", "sell", 3000, 2), headers=alice.headers).json()
            c.post("/orders", json=order("DDD", "buy", 3000, 2), headers=bob.headers)
        got = [json.loads(ws.recv(timeout=10)) for _ in range(2)]
    assert [g["event"]["type"] for g in got] == ["ack", "fill"]
    assert all(g["account_id"] == alice.account_id for g in got)
    assert got[1]["event"]["order_id"] == mine["order_id"]


def test_executions_need_a_key() -> None:
    try:
        with wsc.connect(f"{WS}/ws/executions") as ws:
            ws.recv(timeout=5)
        raise AssertionError("expected the connection to be refused")
    except Exception as e:  # noqa: BLE001 - websockets raises InvalidStatus or ConnectionClosed
        assert "4401" in str(e) or "401" in str(e) or "403" in str(e), e


def test_api_keys_are_stored_only_as_hashes(alice: Account) -> None:
    dump = compose("exec", "-T", "postgres", "sh", "-c",
                   'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t accounts --data-only').stdout
    assert alice.key not in dump
    import hashlib
    assert hashlib.sha256(alice.key.encode()).hexdigest() in dump


def test_no_secret_reaches_an_image_or_a_service_that_does_not_need_it() -> None:
    env = dict(line.split("=", 1) for line in (compose("config", "--environment").stdout.splitlines())
               if "=" in line)
    password = env.get("POSTGRES_PASSWORD") or compose("exec", "-T", "postgres", "printenv",
                                                       "POSTGRES_PASSWORD").stdout.strip()
    assert password
    images = set(re.findall(r"^\s*image:\s*(\S+)", compose("config").stdout, re.M))
    for image in images:
        history = subprocess.run(["docker", "history", "--no-trunc", image], capture_output=True, text=True).stdout
        inspect = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True).stdout
        assert password not in history and password not in inspect, image
    for service in ("api", "matcher", "projector"):
        assert "TIINGO_API_KEY" not in compose("exec", "-T", service, "env").stdout, service


def test_migrations_round_trip_on_a_scratch_database() -> None:
    out = compose("exec", "-T", "api", "python", "-m", "services.db.migrate", "--roundtrip-check")
    assert "roundtrip ok" in out.stdout


def test_the_projection_matches_a_fresh_replay_of_every_journal() -> None:
    verify_projection()
