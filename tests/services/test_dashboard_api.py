"""What the gateway gained for the dashboard (M11), without Redis or Postgres: public
reads, practice accounts and the client IP behind the proxy, the backtest form and the
per-account cap, integer safety, the OpenAPI security scheme, and ping/pong."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from api_fakes import OTHER_KEY, PRACTICE_KEY, SETTINGS, TRADER_KEY, Deps, call, error, make_deps
from research.analytics.tearsheet import check_portfolio_level as research_check
from services.api.app import ERROR_CODES, create_app
from services.common.published import NotPublishable, check_portfolio_level
from services.common.safe_json import MAX_SAFE, find_unsafe, stringify_big

REPO = Path(__file__).resolve().parents[2]
CATALOG = json.loads((REPO / "tests/fixtures/backtest_form/catalog.json").read_text())
FORM = next(c["form"] for c in json.loads((REPO / "tests/fixtures/backtest_form/cases.json").read_text())["cases"]
            if c["name"] == "valid tsmom")
DEMO = dataclasses.replace(SETTINGS, demo_mode=True, practice_per_ip_per_hour=2, trusted_proxies=("10.9.0.0/24",))


@pytest.fixture
def deps() -> Deps:
    d = make_deps()

    async def catalog() -> dict[str, Any]:
        return CATALOG
    d.catalog = catalog
    return d


# --- Public reads -------------------------------------------------------------------------------

def test_instruments_and_config_need_no_key(deps: Deps) -> None:
    body = call(deps, "GET", "/instruments", key=None).json()
    assert body["instruments"] == [{"symbol": "AAA", "tick_size": "0.01", "name": "Aaa Inc"},
                                   {"symbol": "BBB", "tick_size": "0.05", "name": ""}]
    cfg = call(deps, "GET", "/config", key=None).json()
    assert cfg["demo_mode"] is False and cfg["practice_backtest_cap"] == 2
    assert cfg["max_price_ticks"] <= MAX_SAFE and cfg["max_order_qty"] <= MAX_SAFE


def test_backtests_can_be_listed_and_read_without_a_key_but_not_queued(deps: Deps) -> None:
    r = call(deps, "POST", "/backtests", json={"form": FORM}, key=TRADER_KEY)
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    listing = call(deps, "GET", "/backtests", key=None).json()
    assert [x["id"] for x in listing["runs"]] == [run_id] and listing["counts"]["total"] == 1
    assert {f["id"] for f in listing["featured"]} == {"showcase-tsmom", "showcase-xsmom"}
    view = call(deps, "GET", f"/backtests/{run_id}", key=None).json()
    assert view["status"] == "queued" and view["summary"]["real_data"] is True
    assert call(deps, "POST", "/backtests", json={"form": FORM}, key=None).status_code == 401


def test_featured_showcase_runs_are_served_from_the_committed_files(deps: Deps) -> None:
    view = call(deps, "GET", "/backtests/showcase-tsmom", key=None).json()
    on_disk = json.loads((REPO / "research/showcase/tsmom/metrics.json").read_text())
    assert view["featured"] and view["status"] == "done"
    assert view["metrics"] == stringify_big(on_disk)
    assert call(deps, "GET", "/backtests/showcase-tsmom/tearsheet", key=None).text.startswith("<!")


def test_published_metrics_are_portfolio_level_and_the_guard_agrees_with_research(deps: Deps) -> None:
    metrics = json.loads((REPO / "research/showcase/tsmom/metrics.json").read_text())
    symbols = ["SPY", "QQQ", "TLT"]
    check_portfolio_level(metrics, symbols)
    research_check(metrics, symbols)
    for bad in ({**metrics, "SPY": {"x": 1}}, {**metrics, "prices": list(range(100))},
                {**metrics, "series": {**metrics["series"], "close": list(range(100))}}):
        with pytest.raises(NotPublishable):
            check_portfolio_level(bad, symbols)
        with pytest.raises(ValueError):
            research_check(bad, symbols)


def test_a_run_whose_metrics_name_a_symbol_is_never_served(deps: Deps) -> None:
    deps.store.backtests["leaky"] = {"id": "leaky", "account_id": 7, "status": "done", "config": {"data": {}},
                                     "metrics": {"performance": {}, "SPY": [1, 2, 3]}}
    r = call(deps, "GET", "/backtests/leaky", key=None)
    assert r.status_code == 500 and error(r)["code"] == "NOT_PUBLISHABLE"


def test_the_catalog_is_served_and_holds_no_prices(deps: Deps) -> None:
    body = call(deps, "GET", "/backtests/catalog", key=None).json()
    assert {d["id"] for d in body["datasets"]} == {"etf", "synthetic"} and set(body["strategies"]) == {"tsmom", "xsmom"}

    def keys(v: Any) -> set[str]:
        if isinstance(v, dict):
            return set(v) | set().union(*(keys(x) for x in v.values()))
        if isinstance(v, list):
            return set().union(*(keys(x) for x in v)) if v else set()
        return set()
    assert not keys(body["datasets"]) & {"open", "high", "low", "close", "price", "prices", "volume", "dividend",
                                          "adj_close", "bars"}


def test_without_a_catalog_the_form_is_unavailable(deps: Deps) -> None:
    d = make_deps()
    assert call(d, "GET", "/backtests/catalog", key=None).status_code == 503
    r = call(d, "POST", "/backtests", json={"form": FORM})
    assert r.status_code == 503 and error(r)["code"] == "CATALOG_UNAVAILABLE"


def test_failed_runs_count_in_the_total(deps: Deps) -> None:
    for i, status in enumerate(["done", "failed", "failed", "running"]):
        deps.store.backtests[f"r{i}"] = {"id": f"r{i}", "account_id": 7, "status": status, "config": {}, "metrics": None}
    counts = call(deps, "GET", "/backtests", key=None).json()["counts"]
    assert counts == {"total": 4, "queued": 0, "running": 1, "done": 1, "failed": 2}


# --- The backtest form and the practice cap -----------------------------------------------------

def test_an_invalid_form_names_each_field(deps: Deps) -> None:
    r = call(deps, "POST", "/backtests", json={"form": {**FORM, "end": "2030-01-01", "symbols": []}})
    e = error(r)
    assert r.status_code == 422 and {d["field"] for d in e["details"]} == {"end", "symbols"}
    assert call(deps, "POST", "/backtests", json={"form": FORM, "config": {}}).status_code == 422


def test_a_valid_form_queues_the_built_walk_forward(deps: Deps) -> None:
    run_id = call(deps, "POST", "/backtests", json={"form": FORM}).json()["id"]
    cfg = deps.store.backtests[run_id]["config"]
    assert cfg["kind"] == "walkforward" and cfg["data"]["symbols"] == sorted(FORM["symbols"])
    assert deps.queue.jobs == [run_id]


def test_practice_accounts_are_capped_with_a_plain_message(deps: Deps) -> None:
    ids = [call(deps, "POST", "/backtests", json={"form": FORM}, key=PRACTICE_KEY).json()["id"] for _ in range(2)]
    r = call(deps, "POST", "/backtests", json={"form": FORM}, key=PRACTICE_KEY)
    e = error(r)
    assert r.status_code == 429 and e["code"] == "BACKTEST_LIMIT" and e["limit"] == 2
    assert "2 backtests queued or running" in e["message"] and "wait for one to finish" in e["message"]
    deps.store.backtests[ids[0]]["status"] = "done"  # one finishes: room for another
    assert call(deps, "POST", "/backtests", json={"form": FORM}, key=PRACTICE_KEY).status_code == 202
    for _ in range(3):  # ordinary accounts are not capped
        assert call(deps, "POST", "/backtests", json={"form": FORM}, key=OTHER_KEY).status_code == 202


def test_practice_accounts_may_only_use_the_form(deps: Deps) -> None:
    config = {"kind": "bars", "data": {"bars": "/etc/passwd"}, "strategy": {"name": "tsmom"}}
    r = call(deps, "POST", "/backtests", json={"config": config}, key=PRACTICE_KEY)
    assert r.status_code == 403 and error(r)["code"] == "FORBIDDEN"


# --- Practice accounts and the client IP --------------------------------------------------------

def test_practice_accounts_exist_only_in_demo_mode(deps: Deps) -> None:
    r = call(deps, "POST", "/practice-accounts", key=None)
    assert r.status_code == 404 and deps.store.accounts == []


def test_a_practice_account_is_a_trader_whose_key_works(deps: Deps) -> None:
    r = call(deps, "POST", "/practice-accounts", key=None, settings=DEMO)
    assert r.status_code == 201, r.text
    body = r.json()
    [row] = deps.store.accounts
    assert body["role"] == "trader" and body["practice"] is True and row["role"] == "trader" and row["practice"]
    assert body["api_key"] not in json.dumps(row)  # stored as a hash only
    assert call(deps, "GET", "/orders", key=body["api_key"], settings=DEMO).status_code == 200


def test_practice_accounts_are_limited_per_client_ip(deps: Deps) -> None:
    peer = ("203.0.113.5", 1)
    assert [call(deps, "POST", "/practice-accounts", key=None, settings=DEMO, client=peer).status_code
            for _ in range(3)] == [201, 201, 429]
    r = call(deps, "POST", "/practice-accounts", key=None, settings=DEMO, client=("203.0.113.6", 1))
    assert r.status_code == 201  # another address has its own allowance


def test_a_spoofed_forwarded_for_from_a_non_proxy_is_ignored(deps: Deps) -> None:
    """A client cannot dodge the per-IP limit by inventing X-Forwarded-For."""
    peer = ("198.51.100.7", 1)
    codes = [call(deps, "POST", "/practice-accounts", key=None, settings=DEMO, client=peer,
                  headers={"X-Forwarded-For": f"192.0.2.{i}"}).status_code for i in range(3)]
    assert codes == [201, 201, 429]
    assert all("198.51.100.7" in k for k in deps.counter.counts)  # type: ignore[attr-defined]
    assert not any("192.0.2." in k for k in deps.counter.counts)  # type: ignore[attr-defined]


def test_the_trusted_proxys_forwarded_for_is_the_client_ip(deps: Deps) -> None:
    proxy = ("10.9.0.4", 1)
    for client_ip in ("192.0.2.1", "192.0.2.2", "192.0.2.3"):  # three clients behind nginx
        r = call(deps, "POST", "/practice-accounts", key=None, settings=DEMO, client=proxy,
                 headers={"X-Forwarded-For": f"6.6.6.6, {client_ip}"})  # only the proxy's own entry counts
        assert r.status_code == 201
    assert sorted(k.split(":")[2] for k in deps.counter.counts) == ["192.0.2.1", "192.0.2.2", "192.0.2.3"]  # type: ignore[attr-defined]
    codes = [call(deps, "POST", "/practice-accounts", key=None, settings=DEMO, client=proxy,
                  headers={"X-Forwarded-For": "192.0.2.1"}).status_code for _ in range(2)]
    assert codes == [201, 429]


# --- Integer safety -----------------------------------------------------------------------------

def test_prices_and_quantities_above_the_bounds_are_validation_errors(deps: Deps) -> None:
    order = {"symbol": "AAA", "side": "buy", "price": 10**9 + 1, "qty": 1, "client_order_id": "c"}
    assert call(deps, "POST", "/orders", json=order).status_code == 422
    assert call(deps, "POST", "/orders", json={**order, "price": 100, "qty": 2**53}).status_code == 422
    assert deps.matcher.sent == []


def test_position_money_is_text_so_it_survives_any_size(deps: Deps) -> None:
    deps.store.position_rows = [{"symbol": "AAA", "qty": 10**9, "cash": -(10**18) * 7, "open_buy_qty": 0,
                                 "open_sell_qty": 0, "open_notional": 10**17, "mark": 10**9}]
    [p] = call(deps, "GET", "/accounts/7/positions").json()["positions"]
    assert p["cash"] == str(-(10**18) * 7) and p["open_notional"] == str(10**17)
    assert p["pnl"] == str(-(10**18) * 7 + 10**18)


def test_an_unsafe_integer_is_never_sent_rounded(deps: Deps) -> None:
    deps.store.orders[5] = {"order_id": 5, "account_id": 7, "symbol": "AAA", "status": "open", "fills": [],
                            "price": 2**60}
    r = call(deps, "GET", "/orders/5")
    assert r.status_code == 500 and r.json()["error"]["code"] == "UNSAFE_INTEGER" and "2**53" in r.text


def test_journal_timestamps_are_text(deps: Deps) -> None:
    """Nanoseconds since 1970 are about 1.8e18: a browser would round them."""
    ts = 1_761_000_000_123_456_789
    async def trades(symbol: str, limit: int) -> list[dict[str, Any]]:
        return [{"trade_id": 1, "price": 100, "qty": 2, "seq_no": 5, "ts": ts}]
    deps.store.trades = trades  # type: ignore[method-assign]
    deps.store.orders[1] = {"order_id": 1, "account_id": 7, "symbol": "AAA", "status": "filled",
                            "fills": [{"trade_id": 1, "price": 100, "qty": 2, "seq_no": 5, "ts": ts}]}
    assert call(deps, "GET", "/trades/AAA", key=None).json()["trades"][0]["ts"] == str(ts)
    assert call(deps, "GET", "/fills").json()["fills"][0]["ts"] == str(ts)


def test_big_integers_in_metrics_and_configs_become_text(deps: Deps) -> None:
    deps.store.backtests["big"] = {"id": "big", "account_id": 7, "status": "done",
                                   "config": {"analytics": {"capacity": [10**16]}},
                                   "metrics": {"capacity": [{"capital": 10**16, "sharpe": 0.1}]}}
    body = call(deps, "GET", "/backtests/big", key=None).json()
    assert body["metrics"]["capacity"][0]["capital"] == "10000000000000000"
    assert body["config"]["analytics"]["capacity"] == ["10000000000000000"]
    assert find_unsafe(body) is None


def test_the_safety_helpers() -> None:
    assert find_unsafe({"a": [1, {"b": 2**53}]}) == "$.a[1].b"
    assert find_unsafe({"a": 2**53 - 1, "b": -(2**53 - 1), "c": True, "d": 1e300}) is None
    assert stringify_big({"a": [2**53, -(2**53)], "b": 5}) == {"a": [str(2**53), str(-(2**53))], "b": 5}


# --- OpenAPI, listings, WebSocket ---------------------------------------------------------------

def test_openapi_declares_the_key_as_a_security_scheme_on_keyed_routes_only(deps: Deps) -> None:
    doc = call(deps, "GET", "/openapi.json", key=None).json()
    assert doc["components"]["securitySchemes"]["ApiKey"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
        "description": "An account's API key (`mf_<prefix>_<secret>`)."}
    secured = {(m, p) for p, ops in doc["paths"].items() for m, op in ops.items() if op.get("security")}
    public = {("get", "/book/{symbol}"), ("get", "/trades/{symbol}"), ("get", "/instruments"), ("get", "/config"),
              ("get", "/health"), ("get", "/metrics"), ("get", "/backtests"), ("get", "/backtests/catalog"),
              ("get", "/backtests/{run_id}"), ("get", "/backtests/{run_id}/tearsheet"),
              ("post", "/practice-accounts")}
    every = {(m, p) for p, ops in doc["paths"].items() for m in ops}
    assert secured == every - public
    assert call(deps, "GET", "/orders/1", key=None).json()["error"]["code"] == "UNAUTHORIZED"  # same error schema


def test_open_orders_and_fills_are_the_accounts_own(deps: Deps) -> None:
    fill = {"trade_id": 1, "price": 100, "qty": 2, "liquidity": "maker", "seq_no": 4, "symbol": "AAA"}
    deps.store.orders = {
        1: {"order_id": 1, "account_id": 7, "symbol": "AAA", "status": "open", "fills": []},
        2: {"order_id": 2, "account_id": 7, "symbol": "AAA", "status": "filled", "fills": [fill]},
        3: {"order_id": 3, "account_id": 8, "symbol": "AAA", "status": "open", "fills": []}}
    assert [o["order_id"] for o in call(deps, "GET", "/orders").json()["orders"]] == [1]
    assert [o["order_id"] for o in call(deps, "GET", "/orders?status=all").json()["orders"]] == [2, 1]
    assert [f["trade_id"] for f in call(deps, "GET", "/fills").json()["fills"]] == [1]
    assert call(deps, "GET", "/orders", key=None).status_code == 401


def test_a_key_finds_its_own_account(deps: Deps) -> None:
    assert call(deps, "GET", "/accounts/me", key=PRACTICE_KEY).json() == {"account_id": 9, "role": "trader",
                                                                          "practice": True}
    assert call(deps, "GET", "/accounts/me", key=None).status_code == 401


def test_marketdata_answers_a_ping_with_a_pong(deps: Deps) -> None:
    with TestClient(create_app(SETTINGS, deps=deps)) as client, client.websocket_connect("/ws/marketdata/AAA") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_text(json.dumps({"type": "ping", "id": 41}))
        assert ws.receive_json() == {"type": "pong", "id": 41}
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "ping", "id": 2**60}))  # ignored: not a safe integer
        ws.send_text(json.dumps({"type": "ping", "id": 42}))
        assert ws.receive_json() == {"type": "pong", "id": 42}


def test_backtest_progress_is_public(deps: Deps) -> None:
    with TestClient(create_app(SETTINGS, deps=deps)) as client, \
            client.websocket_connect("/ws/backtests/showcase-xsmom") as ws:
        status = ws.receive_json()
        assert status["type"] == "status" and status["status"] == "done"
        assert ws.receive_json() == {"type": "done", "id": "showcase-xsmom"}


def test_every_error_code_in_the_gateway_is_registered() -> None:
    import re
    source = (REPO / "services/api/app.py").read_text()
    used = set(re.findall(r'ApiError\(\d+, "([A-Z_]+)"', source)) | set(re.findall(r'"code": "([A-Z_]+)"', source))
    used |= set(re.findall(r'"([A-Z_]+)": \d{3}', source))  # ERROR_STATUS and the HTTP fallbacks
    assert used <= set(ERROR_CODES), used - set(ERROR_CODES)
