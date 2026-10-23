"""The M11 additions on the real stack: the smoke run leaves no resting orders, the client
IP behind the dashboard's nginx cannot be spoofed, the worker publishes its catalog, and
the demo bot keeps the fictional market trading."""

from __future__ import annotations

import json
import os

import httpx

from .stack import API, Account, client, compose, create_account, eventually

DASHBOARD = os.environ.get("MF_COMPOSE_DASHBOARD", "http://127.0.0.1:8080")


def practice_counter_ips() -> set[str]:
    out = compose("exec", "-T", "redis", "redis-cli", "--scan", "--pattern", "mf:practice:*").stdout
    return {line.split(":")[2] for line in out.split() if line.startswith("mf:practice:")}


def test_the_gateway_smoke_run_leaves_no_resting_orders() -> None:
    a, b = create_account(), create_account()
    out = compose("exec", "-T", "api", "python", "-m", "services.smoke", "--url", "http://localhost:8000",
                  "--key", a.key, "--key2", b.key, "--n", "300", "--concurrency", "8", "--symbol", "SMOKE").stdout
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] == 300 and report["cleanup"]["open_after"] == 0
    with client() as c:
        for acct in (a, b):
            assert c.get("/orders", params={"status": "open", "symbol": "SMOKE"}, headers=acct.headers).json()["orders"] == []
        book = eventually(lambda: (lambda bk: bk if not bk["bids"] and not bk["asks"] else None)(
            c.get("/book/SMOKE").json()), what="an empty SMOKE book")
        assert book["bids"] == [] and book["asks"] == []
        assert c.get("/book/ACME").json()["md_seq"] >= 0  # the demo market was not touched


def test_a_spoofed_forwarded_for_never_reaches_the_practice_limit() -> None:
    compose("exec", "-T", "redis", "sh", "-c", "redis-cli --scan --pattern 'mf:practice:*' | xargs -r redis-cli del")
    spoof = {"X-Forwarded-For": "203.0.113.77"}
    # Through nginx: it overwrites the header with the address it saw.
    assert httpx.post(f"{DASHBOARD}/api/practice-accounts", headers=spoof, timeout=10).status_code == 201
    # Straight to the gateway: not from the trusted proxy, so the header is ignored.
    assert httpx.post(f"{API}/practice-accounts", headers=spoof, timeout=10).status_code == 201
    ips = practice_counter_ips()
    assert ips and "203.0.113.77" not in ips


def test_practice_accounts_are_traders_capped_at_two_backtests() -> None:
    r = httpx.post(f"{DASHBOARD}/api/practice-accounts", timeout=10).json()
    acct = Account(r["account_id"], r["api_key"])
    with client() as c:
        cat = c.get("/backtests/catalog").json()
        syn = next(d for d in cat["datasets"] if d["id"] == "synthetic")
        form = {"dataset": "synthetic", "strategy": "tsmom", "start": syn["start"], "end": syn["end"],
                "symbols": ["AAA", "BBB"], "train_years": 1, "costs": False,
                "params": {"lookback": [60, 120], "vol_window": [20, 60], "target_vol": [0.05], "max_leverage": [1.0]}}
        codes = [c.post("/backtests", json={"form": form}, headers=acct.headers).status_code for _ in range(3)]
        assert codes == [202, 202, 429]
        assert c.post("/backtests", json={"config": {"kind": "bars", "data": {}, "strategy": {}}},
                      headers=acct.headers).status_code == 403


def test_the_worker_publishes_a_catalog_of_dates_and_symbols_only() -> None:
    with client() as c:
        cat = eventually(lambda: (lambda r: r.json() if r.status_code == 200 else None)(c.get("/backtests/catalog")),
                         what="the catalog")
    syn = next(d for d in cat["datasets"] if d["id"] == "synthetic")
    assert syn["real"] is False and len(syn["calendar"]) == 2500 and syn["symbols"][0]["symbol"] == "AAA"
    for d in cat["datasets"]:
        assert set(d) <= {"id", "label", "source", "real", "path", "rates", "benchmark", "survivorship", "start",
                          "end", "calendar", "symbols", "synthetic_bars"}


def test_the_demo_bot_keeps_the_fictional_market_trading() -> None:
    with client() as c:
        for sym in ("ACME", "GLOBEX", "INITECH"):
            book = eventually(lambda: (lambda bk: bk if bk["bids"] and bk["asks"] else None)(
                c.get(f"/book/{sym}").json()), timeout=60, what=f"quotes on {sym}")
            assert book["bids"][0][0] < book["asks"][0][0]
            eventually(lambda: c.get(f"/trades/{sym}?limit=1").json()["trades"], timeout=60, what=f"a trade on {sym}")
    assert "AAPL" not in httpx.get(f"{DASHBOARD}/api/instruments", timeout=10).text


def test_the_dashboard_is_served_with_its_routes_and_security_headers() -> None:
    for path in ("/", "/market/ACME", "/backtests/showcase-tsmom"):
        r = httpx.get(f"{DASHBOARD}{path}", timeout=10)
        assert r.status_code == 200 and '<div id="root">' in r.text
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert httpx.get(f"{DASHBOARD}/api/health", timeout=10).json()["status"] == "ok"
    assert "securitySchemes" in httpx.get(f"{DASHBOARD}/openapi.json", timeout=10).text
