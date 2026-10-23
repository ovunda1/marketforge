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


class MatcherTimeout(Exception):
    """No reply within the request timeout: the outcome is unknown."""


class MatcherClient(Protocol):
    async def request(self, req: Request) -> Reply: ...


class Store(Protocol):
    async def account_by_prefix(self, prefix: str) -> Account | None: ...
    async def wait_for(self, symbol: str, seq_no: int, timeout_s: float) -> bool: ...
    async def get_order(self, order_id: int) -> dict[str, Any] | None: ...
    async def trades(self, symbol: str, limit: int) -> list[dict[str, Any]]: ...
    async def positions(self, account_id: int) -> list[dict[str, Any]]: ...
    async def create_backtest(self, run_id: str, account_id: int, config: dict[str, Any]) -> None: ...
    async def get_backtest(self, run_id: str) -> dict[str, Any] | None: ...


class BookCache(Protocol):
    async def get_book(self, symbol: str) -> dict[str, Any] | None: ...


class RateLimiter(Protocol):
    async def allow(self, account_id: int) -> bool: ...


class BacktestQueue(Protocol):
    async def enqueue(self, run_id: str) -> None: ...


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
    start: Callable[[], Awaitable[None]] | None = None
    stop: Callable[[], Awaitable[None]] | None = None
    extra: dict[str, Any] = field(default_factory=dict)
