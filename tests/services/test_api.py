"""The gateway (M10) without Redis or Postgres: fake matcher, store and caches injected
through `Deps`, requests through httpx's in-process ASGI transport (no sockets)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.auth import generate_api_key
from services.api.deps import Account, Deps, MatcherTimeout
from services.api.hub import Hub
from services.common.config import Settings
from services.common.protocol import Reply, Request

TRADER_KEY, TRADER_PREFIX, TRADER_HASH = generate_api_key()
OTHER_KEY, OTHER_PREFIX, OTHER_HASH = generate_api_key()
ADMIN_KEY, ADMIN_PREFIX, ADMIN_HASH = generate_api_key()
ACCOUNTS = {TRADER_PREFIX: Account(7, "trader", TRADER_HASH), OTHER_PREFIX: Account(8, "trader", OTHER_HASH),
            ADMIN_PREFIX: Account(1, "admin", ADMIN_HASH)}


class FakeMatcher:
    def __init__(self) -> None:
        self.sent: list[Request] = []
        self.reply: Reply | Exception | None = None

    async def request(self, req: Request) -> Reply:
        self.sent.append(req)
        if isinstance(self.reply, Exception):
            raise self.reply
        assert self.reply is not None
        return self.reply


class FakeStore:
    def __init__(self) -> None:
        self.orders: dict[int, dict[str, Any]] = {}
        self.waited: list[tuple[str, int]] = []
        self.backtests: dict[str, dict[str, Any]] = {}

    async def account_by_prefix(self, prefix: str) -> Account | None:
        return ACCOUNTS.get(prefix)

    async def wait_for(self, symbol: str, seq_no: int, timeout_s: float) -> bool:
        self.waited.append((symbol, seq_no))
        return True

    async def get_order(self, order_id: int) -> dict[str, Any] | None:
        return self.orders.get(order_id)

    async def trades(self, symbol: str, limit: int) -> list[dict[str, Any]]:
        return [{"trade_id": 1, "price": 100, "qty": 2}][:limit]

    async def positions(self, account_id: int) -> list[dict[str, Any]]:
        return [{"symbol": "AAA", "qty": 3, "cash": -300, "open_buy_qty": 0, "open_sell_qty": 0,
                 "open_notional": 0, "mark": 101}]

    async def create_backtest(self, run_id: str, account_id: int, config: dict[str, Any]) -> None:
        self.backtests[run_id] = {"id": run_id, "account_id": account_id, "status": "queued", "config": config}

    async def get_backtest(self, run_id: str) -> dict[str, Any] | None:
        return self.backtests.get(run_id)


class FakeCache:
    async def get_book(self, symbol: str) -> dict[str, Any] | None:
        if symbol != "AAA":
            return None
        return {"type": "snapshot", "symbol": "AAA", "epoch": 1, "md_seq": 4, "seq_no": 9,
                "bids": [[100 - i, 1, 1] for i in range(20)], "asks": [[101 + i, 1, 1] for i in range(20)]}


class FakeLimiter:
    def __init__(self) -> None:
        self.allow_requests = True

    async def allow(self, account_id: int) -> bool:
        return self.allow_requests


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    async def enqueue(self, run_id: str) -> None:
        self.jobs.append(run_id)


@pytest.fixture
def deps() -> Deps:
    return Deps(matcher=FakeMatcher(), store=FakeStore(), cache=FakeCache(), limiter=FakeLimiter(), hub=Hub(),
                queue=FakeQueue())


SETTINGS = Settings(symbols=("AAA", "BBB"), request_timeout_s=0.5)


def call(deps: Deps, method: str, path: str, key: str | None = TRADER_KEY, **kw: Any) -> httpx.Response:
    async def go() -> httpx.Response:
        app = create_app(SETTINGS, deps=deps)
        headers = {"X-API-Key": key} if key else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            return await client.request(method, path, headers=headers, **kw)
    return asyncio.run(go())


ORDER = {"symbol": "AAA", "side": "buy", "type": "limit", "tif": "gtc", "price": 100, "qty": 5,
         "client_order_id": "c1"}
ACK = {"type": "ack", "seq_no": 9, "order_id": 42, "side": "buy", "order_type": "limit", "tif": "gtc",
       "post_only": False, "stp": "cancel_newest", "price": 100, "qty": 5}
FILL = {"type": "fill", "seq_no": 9, "trade_id": 3, "order_id": 42, "account_id": 7, "side": "buy",
        "liquidity": "taker", "price": 100, "qty": 2, "leaves_qty": 3, "cum_qty": 2}


def error(resp: httpx.Response) -> dict[str, Any]:
    body = resp.json()
    assert set(body) == {"error"} and {"code", "message", "request_id"} <= set(body["error"]), body
    return body["error"]


def test_requests_without_a_valid_key_are_unauthorized(deps: Deps) -> None:
    assert error(call(deps, "GET", "/orders/1", key=None))["code"] == "UNAUTHORIZED"
    r = call(deps, "GET", "/orders/1", key=TRADER_KEY[:-1] + "x")
    assert r.status_code == 401 and error(r)["code"] == "UNAUTHORIZED"


def test_an_accepted_order_returns_its_state_from_the_events(deps: Deps) -> None:
    deps.matcher.reply = Reply(request_id="x", status="ok", symbol="AAA", order_id=42, seq_no=9,
                               events=[ACK, FILL])
    r = call(deps, "POST", "/orders", json=ORDER)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["order_id"], body["status"], body["cum_qty"], body["leaves_qty"], body["seq_no"]) == (
        42, "partially_filled", 2, 3, 9)
    assert body["fills"] == [{"trade_id": 3, "price": 100, "qty": 2, "liquidity": "taker", "seq_no": 9}]
    [sent] = deps.matcher.sent
    assert (sent.op, sent.account_id, sent.key, sent.symbol) == ("new", 7, "c1", "AAA")
    assert sent.params["price"] == 100 and sent.deadline_ns > 0  # reply_to is the matcher client's


def test_an_engine_reject_is_a_422_with_its_reason_code(deps: Deps) -> None:
    deps.matcher.reply = Reply(request_id="x", status="rejected", symbol="AAA", order_id=43, seq_no=10,
                               reason="MAX_QTY", events=[{"type": "reject", "seq_no": 10, "order_id": 43,
                                                          "reason": "MAX_QTY"}])
    r = call(deps, "POST", "/orders", json=ORDER)
    e = error(r)
    assert r.status_code == 422 and (e["code"], e["reason"], e["order_id"], e["seq_no"]) == (
        "ORDER_REJECTED", "MAX_QTY", 43, 10)


@pytest.mark.parametrize("code,status", [("IDEMPOTENCY_CONFLICT", 409), ("UNKNOWN_SYMBOL", 404),
                                         ("NOT_FOUND", 404), ("SYMBOL_HALTED", 503), ("EXPIRED", 504)])
def test_matcher_errors_map_to_http_statuses(deps: Deps, code: str, status: int) -> None:
    deps.matcher.reply = Reply(request_id="x", status="error", code=code, message="m")
    r = call(deps, "POST", "/orders", json=ORDER)
    assert r.status_code == status and error(r)["code"] == code


def test_a_matcher_timeout_is_a_504_saying_the_outcome_is_unknown(deps: Deps) -> None:
    deps.matcher.reply = MatcherTimeout()
    r = call(deps, "POST", "/orders", json=ORDER)
    e = error(r)
    assert r.status_code == 504 and e["code"] == "MATCHER_TIMEOUT" and "client_order_id" in e["message"]


def test_a_replayed_order_is_read_back_from_the_projection(deps: Deps) -> None:
    deps.matcher.reply = Reply(request_id="x", status="ok", symbol="AAA", order_id=42, seq_no=9, replayed=True)
    deps.store.orders[42] = {"order_id": 42, "account_id": 7, "symbol": "AAA", "status": "open", "fills": []}
    r = call(deps, "POST", "/orders", json=ORDER)
    assert r.status_code == 200 and r.headers["Idempotent-Replayed"] == "true"
    assert r.json()["order_id"] == 42 and deps.store.waited == [("AAA", 9)]


def test_a_float_price_is_a_validation_error_never_rounded(deps: Deps) -> None:
    r = call(deps, "POST", "/orders", json=ORDER | {"price": 100.5})
    assert r.status_code == 422 and error(r)["code"] == "VALIDATION_ERROR"
    assert deps.matcher.sent == []
    assert call(deps, "POST", "/orders", json={k: v for k, v in ORDER.items() if k != "client_order_id"}
                ).status_code == 422


def test_another_accounts_order_is_not_found(deps: Deps) -> None:
    deps.store.orders[5] = {"order_id": 5, "account_id": 8, "symbol": "AAA", "status": "open", "fills": []}
    assert call(deps, "GET", "/orders/5").status_code == 404
    assert call(deps, "GET", "/orders/5", key=OTHER_KEY).status_code == 200


def test_get_order_waits_for_the_projection_when_asked(deps: Deps) -> None:
    deps.store.orders[5] = {"order_id": 5, "account_id": 7, "symbol": "AAA", "status": "open", "fills": []}
    assert call(deps, "GET", "/orders/5?min_seq_no=12").status_code == 200
    assert deps.store.waited == [("AAA", 12)]


def test_book_depth_is_cut_from_the_cached_snapshot(deps: Deps) -> None:
    r = call(deps, "GET", "/book/AAA?depth=3")
    body = r.json()
    assert len(body["bids"]) == len(body["asks"]) == 3 and body["md_seq"] == 4
    assert call(deps, "GET", "/book/ZZZ").status_code == 404


def test_admin_endpoints_need_the_admin_role(deps: Deps) -> None:
    deps.matcher.reply = Reply(request_id="x", status="ok", results=[{"symbol": "AAA", "seq_no": 3, "events": []}])
    body = {"engaged": True, "account_id": 8, "cancel_open": True}
    assert call(deps, "POST", "/admin/kill-switch", json=body).status_code == 403
    r = call(deps, "POST", "/admin/kill-switch", key=ADMIN_KEY, json=body)
    assert r.status_code == 200, r.text
    [sent] = deps.matcher.sent
    assert (sent.op, sent.symbol, sent.params["target_account"]) == ("kill_switch", None, 8)


def test_the_rate_limit_is_a_429(deps: Deps) -> None:
    deps.limiter.allow_requests = False
    r = call(deps, "POST", "/orders", json=ORDER)
    assert r.status_code == 429 and error(r)["code"] == "RATE_LIMITED"


def test_positions_are_only_for_the_own_account(deps: Deps) -> None:
    assert call(deps, "GET", "/accounts/8/positions").status_code == 404
    [p] = call(deps, "GET", "/accounts/7/positions").json()["positions"]
    assert p["pnl"] == -300 + 3 * 101  # cash + qty * mark, integer ticks x qty


def test_a_backtest_is_queued_with_its_config(deps: Deps) -> None:
    config = {"kind": "event", "seed": 1, "data": {"synthetic": {"seed": 1, "duration_ns": 10**9}},
              "strategy": {"name": "obmom", "params": {}}}
    r = call(deps, "POST", "/backtests", json={"config": config})
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert deps.queue.jobs == [run_id] and deps.store.backtests[run_id]["config"] == config
    assert call(deps, "GET", f"/backtests/{run_id}").json()["status"] == "queued"
    assert call(deps, "GET", f"/backtests/{run_id}", key=OTHER_KEY).status_code == 404
    assert call(deps, "POST", "/backtests", json={"config": {"kind": "nope"}}).status_code == 422


def test_the_openapi_document_lists_every_endpoint(deps: Deps) -> None:
    paths = call(deps, "GET", "/openapi.json", key=None).json()["paths"]
    for path in ["/orders", "/orders/{order_id}", "/book/{symbol}", "/trades/{symbol}",
                 "/accounts/{account_id}/positions", "/backtests", "/backtests/{run_id}",
                 "/admin/risk-limits", "/admin/kill-switch", "/admin/session", "/health", "/metrics"]:
        assert path in paths, path
