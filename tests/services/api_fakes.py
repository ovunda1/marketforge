"""Fakes for the gateway's dependencies (M10, M11): matcher, store, caches and queue,
injected through `Deps`; requests go through httpx's in-process ASGI transport (no
sockets). Shared by test_api.py and test_dashboard_api.py."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from services.common.instruments import Instrument

from services.api.app import create_app
from services.api.auth import generate_api_key
from services.api.deps import Account, Deps, MatcherTimeout
from services.api.hub import Hub
from services.common.config import Settings
from services.common.protocol import Reply, Request

TRADER_KEY, TRADER_PREFIX, TRADER_HASH = generate_api_key()
OTHER_KEY, OTHER_PREFIX, OTHER_HASH = generate_api_key()
ADMIN_KEY, ADMIN_PREFIX, ADMIN_HASH = generate_api_key()
PRACTICE_KEY, PRACTICE_PREFIX, PRACTICE_HASH = generate_api_key()
ACCOUNTS = {TRADER_PREFIX: Account(7, "trader", TRADER_HASH), OTHER_PREFIX: Account(8, "trader", OTHER_HASH),
            ADMIN_PREFIX: Account(1, "admin", ADMIN_HASH), PRACTICE_PREFIX: Account(9, "trader", PRACTICE_HASH, True)}


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
        self.accounts: list[dict[str, Any]] = []
        self.position_rows = [{"symbol": "AAA", "qty": 3, "cash": -300, "open_buy_qty": 0, "open_sell_qty": 0,
                               "open_notional": 0, "mark": 101}]

    async def account_by_prefix(self, prefix: str) -> Account | None:
        for row in self.accounts:
            if row["api_key_prefix"] == prefix:
                return Account(row["id"], row["role"], row["api_key_hash"], row["practice"])
        return ACCOUNTS.get(prefix)

    async def create_account(self, row: dict[str, Any]) -> int:
        self.accounts.append({**row, "id": 100 + len(self.accounts)})
        return self.accounts[-1]["id"]  # type: ignore[no-any-return]

    async def list_orders(self, account_id: int, open_only: bool, symbol: str | None,
                          limit: int) -> list[dict[str, Any]]:
        rows = [o for o in self.orders.values() if o["account_id"] == account_id
                and (not open_only or o["status"] in ("open", "partially_filled"))
                and (symbol is None or o["symbol"] == symbol)]
        return sorted(rows, key=lambda o: -o["order_id"])[:limit]

    async def fills(self, account_id: int, symbol: str | None, limit: int) -> list[dict[str, Any]]:
        return [f for o in self.orders.values() if o["account_id"] == account_id for f in o.get("fills", [])][:limit]

    async def wait_for(self, symbol: str, seq_no: int, timeout_s: float) -> bool:
        self.waited.append((symbol, seq_no))
        return True

    async def get_order(self, order_id: int) -> dict[str, Any] | None:
        return self.orders.get(order_id)

    async def trades(self, symbol: str, limit: int) -> list[dict[str, Any]]:
        return [{"trade_id": 1, "price": 100, "qty": 2}][:limit]

    async def positions(self, account_id: int) -> list[dict[str, Any]]:
        return self.position_rows

    async def create_backtest(self, run_id: str, account_id: int, config: dict[str, Any],
                              cap: int | None = None) -> bool:
        active = sum(1 for b in self.backtests.values()
                     if b["account_id"] == account_id and b["status"] in ("queued", "running"))
        if cap is not None and active >= cap:
            return False
        self.backtests[run_id] = {"id": run_id, "account_id": account_id, "status": "queued", "config": config,
                                  "metrics": None}
        return True

    async def get_backtest(self, run_id: str) -> dict[str, Any] | None:
        return self.backtests.get(run_id)

    async def list_backtests(self, limit: int) -> list[dict[str, Any]]:
        return list(self.backtests.values())[::-1][:limit]

    async def backtest_counts(self) -> dict[str, int]:
        by = [b["status"] for b in self.backtests.values()]
        return {"total": len(by), **{s: by.count(s) for s in ("queued", "running", "done", "failed")}}


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


def make_deps() -> Deps:
    return Deps(matcher=FakeMatcher(), store=FakeStore(), cache=FakeCache(), limiter=FakeLimiter(), hub=Hub(),
                queue=FakeQueue())


SETTINGS = Settings(symbols=("AAA", "BBB"), request_timeout_s=0.5,
                    instruments=(Instrument("AAA", "0.01", name="Aaa Inc"), Instrument("BBB", "0.05")))


def call(deps: Deps, method: str, path: str, key: str | None = TRADER_KEY, settings: Settings = SETTINGS,
         client: tuple[str, int] = ("127.0.0.1", 123), headers: dict[str, str] | None = None,
         **kw: Any) -> httpx.Response:
    async def go() -> httpx.Response:
        app = create_app(settings, deps=deps)
        h = {"X-API-Key": key} if key else {}
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.request(method, path, headers={**h, **(headers or {})}, **kw)
    return asyncio.run(go())


def error(resp: httpx.Response) -> dict[str, Any]:
    body = resp.json()
    assert set(body) == {"error"} and {"code", "message", "request_id"} <= set(body["error"]), body
    return body["error"]


