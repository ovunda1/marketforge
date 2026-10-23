"""What the gateway depends on (M10), as small interfaces. `create_app` takes a `Deps`;
tests pass fakes, `build_deps` wires the real ones (Redis, Postgres, arq)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from services.api.hub import Hub
from services.common.protocol import Reply, Request


@dataclass(frozen=True)
class Account:
    account_id: int
    role: str
    api_key_hash: str
    practice: bool = False  # M11: a self-service account from the dashboard (demo mode)


class MatcherTimeout(Exception):
    """No reply within the request timeout: the outcome is unknown."""


class MatcherClient(Protocol):
    async def request(self, req: Request) -> Reply: ...


class Store(Protocol):
    async def account_by_prefix(self, prefix: str) -> Account | None: ...
    async def create_account(self, row: dict[str, Any]) -> int: ...
    async def wait_for(self, symbol: str, seq_no: int, timeout_s: float) -> bool: ...
    async def get_order(self, order_id: int) -> dict[str, Any] | None: ...
    async def list_orders(self, account_id: int, open_only: bool, symbol: str | None,
                          limit: int) -> list[dict[str, Any]]: ...
    async def fills(self, account_id: int, symbol: str | None, limit: int) -> list[dict[str, Any]]: ...
    async def trades(self, symbol: str, limit: int) -> list[dict[str, Any]]: ...
    async def positions(self, account_id: int) -> list[dict[str, Any]]: ...
    async def create_backtest(self, run_id: str, account_id: int, config: dict[str, Any],
                              cap: int | None = None) -> bool: ...
    async def get_backtest(self, run_id: str) -> dict[str, Any] | None: ...
    async def list_backtests(self, limit: int) -> list[dict[str, Any]]: ...
    async def backtest_counts(self) -> dict[str, int]: ...


class BookCache(Protocol):
    async def get_book(self, symbol: str) -> dict[str, Any] | None: ...


class RateLimiter(Protocol):
    async def allow(self, account_id: int) -> bool: ...


class BacktestQueue(Protocol):
    async def enqueue(self, run_id: str) -> None: ...


class Counter(Protocol):
    async def incr(self, key: str, ttl_s: int) -> int: ...


class MemoryCounter:
    """Counts within one process (tests, a single worker); the stack uses Redis."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str, ttl_s: int) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


async def _no_catalog() -> dict[str, Any] | None:
    return None


async def _healthy() -> dict[str, str]:
    return {"matcher": "up", "redis": "up", "database": "up"}


@dataclass
class Deps:
    matcher: MatcherClient
    store: Store
    cache: BookCache
    limiter: RateLimiter
    hub: Hub
    queue: BacktestQueue
    health: Callable[[], Awaitable[dict[str, str]]] = _healthy
    counter: Counter = field(default_factory=MemoryCounter)
    catalog: Callable[[], Awaitable[dict[str, Any] | None]] = _no_catalog  # the worker's data catalog
    start: Callable[[], Awaitable[None]] | None = None
    stop: Callable[[], Awaitable[None]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)
