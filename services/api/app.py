"""The gateway (M10): FastAPI REST and WebSocket in front of the single matcher.

Run with several worker processes (`uvicorn --factory services.api.app:create_app
--workers N`). No worker holds an engine: order entry and admin commands go to the
matcher over Redis (services/api/real.py); reads come from the Postgres projection and
the Redis book cache; WebSocket clients are fed from Redis pub/sub through the `Hub`.

Errors share one schema: ``{"error": {"code", "message", "request_id", ...}}``, with
`reason` holding the engine's reject code when the engine rejected a command.

M11 (the dashboard): public reads for instruments, the catalog and backtest results;
practice accounts in demo mode; an OpenAPI security scheme for the key; every integer
in a response either bounded or a string (services/common/safe_json.py).
"""

# No `from __future__ import annotations`: FastAPI reads the endpoint annotations at
# runtime, and the dependency aliases below are local to create_app.
import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request, Security, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyHeader
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocketDisconnect, WebSocketState

from services.api.auth import new_account_row, split_api_key, verify_api_key
from services.api.deps import Account, Deps, MatcherTimeout
from services.api.hub import CLOSED, RESNAPSHOT, Subscriber
from services.api.proxies import TrustedProxies
from services.common import backtest_form, keys
from services.common.config import Settings
from services.common.protocol import Reply
from services.common.protocol import Request as MatcherRequest
from services.common.published import NotPublishable, public_metrics
from services.common.safe_json import MAX_ORDER_QTY, MAX_PRICE_TICKS, MAX_SAFE, find_unsafe, stringify_big

log = logging.getLogger("mf.api")

HTTP_REQUESTS = Counter("mf_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_SECONDS = Histogram("mf_http_request_seconds", "HTTP request latency", ["method", "route"])
WS_SLOW_CLOSED = Counter("mf_ws_slow_consumers_total", "WebSocket clients closed as slow consumers", ["channel"])
WS_RESNAPSHOTS = Counter("mf_ws_resnapshots_total", "Snapshots resent after a slow-consumer overflow")

ERROR_STATUS = {"IDEMPOTENCY_CONFLICT": 409, "UNKNOWN_SYMBOL": 404, "NOT_FOUND": 404, "SYMBOL_HALTED": 503,
                "ENGINE_FAILED": 503, "EXPIRED": 504, "BAD_REQUEST": 400}
WS_UNAUTHORIZED, WS_NOT_FOUND, WS_SLOW, WS_INTERNAL = 4401, 4404, 4008, 1011

# Every error code the gateway can return (the dashboard maps each one, and every engine
# reject reason, to a plain-language message with a suggested fix; a test compares).
ERROR_CODES = ("UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND", "METHOD_NOT_ALLOWED", "HTTP_ERROR", "BAD_REQUEST",
               "VALIDATION_ERROR", "UNKNOWN_SYMBOL", "ORDER_REJECTED", "ADMIN_REJECTED", "IDEMPOTENCY_CONFLICT",
               "RATE_LIMITED", "PRACTICE_LIMIT", "BACKTEST_LIMIT", "SYMBOL_HALTED", "ENGINE_FAILED", "EXPIRED",
               "MATCHER_TIMEOUT", "PROJECTION_LAGGING", "BOOK_UNAVAILABLE", "CATALOG_UNAVAILABLE",
               "NOT_PUBLISHABLE", "UNSAFE_INTEGER", "ERROR")


class SafeJSONResponse(JSONResponse):
    """JSON that a JavaScript client reads without rounding: a payload still holding an
    integer beyond 2**53 - 1 becomes a 500 UNSAFE_INTEGER instead (loud, never rounded)."""

    def render(self, content: Any) -> bytes:
        hit = find_unsafe(content)
        if hit:
            log.error("refusing to send an unsafe integer at %s", hit)
            self.status_code = 500
            content = {"error": {"code": "UNSAFE_INTEGER", "message": f"an integer at {hit} is beyond 2**53 - 1"}}
        return super().render(content)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


# --- Models -------------------------------------------------------------------------------------

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Upper bounds keep every per-order integer far inside JavaScript's safe range (M11);
# values the engine would reject (zero, negative) still reach it, for its reject code.
Price = Annotated[StrictInt, Field(ge=-MAX_SAFE, le=MAX_PRICE_TICKS)]
Qty = Annotated[StrictInt, Field(ge=-MAX_SAFE, le=MAX_ORDER_QTY)]


class OrderIn(Strict):
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["limit", "market"] = "limit"
    tif: Literal["gtc", "ioc", "fok", "day"] = "gtc"
    price: Price = 0  # ticks; a float is a validation error, never rounded
    qty: Qty
    post_only: StrictBool = False
    stp: Literal["cancel_newest", "cancel_oldest", "cancel_both"] = "cancel_newest"
    client_order_id: str = Field(min_length=1, max_length=64)


class ReplaceIn(Strict):
    price: Price
    qty: Qty


class FillOut(BaseModel):
    trade_id: int
    price: int
    qty: int
    liquidity: str
    seq_no: int


class OrderOut(BaseModel):
    order_id: int
    status: str
    symbol: str | None = None
    client_order_id: str | None = None
    side: str | None = None
    type: str | None = None
    tif: str | None = None
    price: int | None = None
    qty: int | None = None
    leaves_qty: int | None = None
    cum_qty: int | None = None
    reject_reason: str | None = None
    cancel_reason: str | None = None  # M11: user, unfilled, self_trade, expired, kill_switch
    seq_no: int | None = None
    fills: list[FillOut] = []


class RiskLimitsIn(Strict):
    symbol: str | None = None  # None: every symbol
    scope: Literal["instrument", "default", "account"]
    account_id: StrictInt | None = None
    limits: dict[str, StrictInt | None]


class KillSwitchIn(Strict):
    engaged: StrictBool
    account_id: StrictInt | None = None  # None: the global switch
    cancel_open: StrictBool = False
    symbol: str | None = None


class SessionIn(Strict):
    state: Literal["pre_open", "continuous", "halted", "closed"]
    symbol: str | None = None


class BacktestIn(Strict):
    """Exactly one of: `config`, the `make backtest` schema as JSON; or `form`, the
    dashboard's validated form (services/common/backtest_form.py). Practice accounts may
    only use the form."""
    config: dict[str, Any] | None = None
    form: dict[str, Any] | None = None


# --- Helpers ------------------------------------------------------------------------------------

def ns_text(ts: Any) -> str | None:
    """A journal timestamp (ns since 1970, about 1.8e18: beyond 2**53) as text."""
    return None if ts is None else str(ts)


def order_from_events(events: list[dict[str, Any]], base: dict[str, Any]) -> dict[str, Any]:
    """An order's state after its command, from the command's events."""
    o = dict(base)
    o.setdefault("fills", [])
    for e in events:
        t = e["type"]
        if t == "ack":
            o.update(side=e["side"], type=e["order_type"], tif=e["tif"], price=e["price"], qty=e["qty"],
                     leaves_qty=e["qty"], cum_qty=0, status="open")
        elif t == "reject":
            o.update(status="rejected", reject_reason=e["reason"], leaves_qty=0)
        elif t == "fill" and e["order_id"] == o["order_id"]:
            o["fills"].append({k: e[k] for k in ("trade_id", "price", "qty", "liquidity", "seq_no")})
            o.update(leaves_qty=e["leaves_qty"], cum_qty=e["cum_qty"],
                     status="filled" if e["leaves_qty"] == 0 else "partially_filled")
        elif t == "cancelled" and e["order_id"] == o["order_id"]:
            o.update(status="cancelled", leaves_qty=0, cancelled_qty=e["cancelled_qty"], cancel_reason=e["reason"])
        elif t == "replaced":
            cum = e["qty"] - e["leaves_qty"]
            o.update(price=e["price"], qty=e["qty"], leaves_qty=e["leaves_qty"], cum_qty=cum,
                     status="partially_filled" if cum else "open")
    return o


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    symbols = set(settings.symbols)
    worker = str(os.getpid())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal deps
        if deps is None:
            from services.api.real import build_deps

            deps = build_deps(settings)
        if deps.start:
            await deps.start()
        yield
        if deps.stop:
            await deps.stop()

    app = FastAPI(title="MarketForge", version="0.11.0", lifespan=lifespan, default_response_class=SafeJSONResponse,
                  description="Simulated exchange: REST and WebSocket gateway in front of a single matcher. "
                              "Prices are integer ticks, quantities integer units. Every symbol is fictional. "
                              "Authorize with an API key (in demo mode, POST /practice-accounts makes one).")
    if deps is not None:
        injected = deps

        @asynccontextmanager
        async def lifespan_injected(app: FastAPI) -> AsyncIterator[None]:
            if injected.start:
                await injected.start()
            yield
            if injected.stop:
                await injected.stop()
        app.router.lifespan_context = lifespan_injected

    def D() -> Deps:
        assert deps is not None
        return deps

    # --- Errors and middleware ------------------------------------------------------------------

    def error_response(request_id: str, status: int, code: str, message: str, **extra: Any) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message,
                                                                   "request_id": request_id, **extra}})

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request.state.request_id, exc.status, exc.code, exc.message, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return error_response(request.state.request_id, 422, "VALIDATION_ERROR", "invalid request", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error_response(request.state.request_id, exc.status_code, code, str(exc.detail))

    @app.middleware("http")
    async def _context(request: Request, call_next: Any) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        start = time.perf_counter()
        response: Response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_SECONDS.labels(request.method, route).observe(time.perf_counter() - start)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Worker"] = worker
        return response

    # --- Auth -----------------------------------------------------------------------------------

    cache: dict[str, tuple[Account | None, float]] = {}

    async def lookup(key: str | None) -> Account | None:
        if not key:
            return None
        prefix = split_api_key(key)
        if prefix is None:
            return None
        hit = cache.get(prefix)
        if hit is None or hit[1] < time.monotonic():
            hit = (await D().store.account_by_prefix(prefix), time.monotonic() + 30)
            cache[prefix] = hit
        account = hit[0]
        return account if account is not None and verify_api_key(key, account.api_key_hash) else None

    api_key = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="ApiKey",
                           description="An account's API key (`mf_<prefix>_<secret>`).")

    async def authenticated(x_api_key: Annotated[str | None, Security(api_key)] = None) -> Account:
        account = await lookup(x_api_key)
        if account is None:
            raise ApiError(401, "UNAUTHORIZED", "a valid X-API-Key header is required")
        if not await D().limiter.allow(account.account_id):
            raise ApiError(429, "RATE_LIMITED", "too many requests for this account")
        return account

    async def admin(account: Annotated[Account, Depends(authenticated)]) -> Account:
        if account.role != "admin":
            raise ApiError(403, "FORBIDDEN", "admin role required")
        return account

    Trader = Annotated[Account, Depends(authenticated)]
    Admin = Annotated[Account, Depends(admin)]

    def known(symbol: str | None) -> None:
        if symbol is not None and symbol not in symbols:
            raise ApiError(404, "UNKNOWN_SYMBOL", f"unknown symbol {symbol!r}")

    async def send(req: MatcherRequest) -> Reply:
        try:
            reply = await D().matcher.request(req)
        except MatcherTimeout:
            raise ApiError(504, "MATCHER_TIMEOUT", "no answer from the matcher in time: the outcome is unknown. "
                                                   "Retry with the same client_order_id (or Idempotency-Key), "
                                                   "or GET the order.") from None
        if reply.status == "error":
            code = reply.code or "ERROR"
            raise ApiError(ERROR_STATUS.get(code, 500), code, reply.message or code)
        return reply

    def rejected(reply: Reply, admin_cmd: bool = False) -> ApiError:
        if admin_cmd:
            return ApiError(422, "ADMIN_REJECTED", f"rejected by the engine: {reply.reason}", reason=reply.reason)
        status = 409 if reply.reason == "UNKNOWN_ORDER" else 422
        return ApiError(status, "ORDER_REJECTED", f"rejected by the engine: {reply.reason}", reason=reply.reason,
                        order_id=reply.order_id, seq_no=reply.seq_no)

    def request(account: Account, op: str, **kw: Any) -> MatcherRequest:
        return MatcherRequest(request_id=uuid.uuid4().hex, reply_to="", deadline_ns=time.time_ns()
                              + int(settings.request_timeout_s * 1e9), account_id=account.account_id, op=op, **kw)

    async def read_order(order_id: int, account: Account, min_seq_no: int | None) -> dict[str, Any]:
        store = D().store
        order = await store.get_order(order_id)
        if order is None and min_seq_no is not None:  # not projected yet
            deadline = time.monotonic() + settings.request_timeout_s
            while order is None and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
                order = await store.get_order(order_id)
        if order is None or order["account_id"] != account.account_id:
            raise ApiError(404, "NOT_FOUND", f"no order {order_id} for this account")
        if min_seq_no is not None:
            if not await store.wait_for(order["symbol"], min_seq_no, settings.request_timeout_s):
                raise ApiError(504, "PROJECTION_LAGGING", f"the projection has not reached seq_no {min_seq_no}")
            order = await store.get_order(order_id) or order
        return order

    # --- Orders ---------------------------------------------------------------------------------

    @app.post("/orders", status_code=201, response_model=OrderOut, tags=["orders"],
              responses={200: {"description": "an idempotent retry: the original order"}})
    async def submit(body: OrderIn, account: Trader, response: Response) -> Any:
        """Submits an order. Risk checks run in the engine: 201 with the order's state, or
        422 ORDER_REJECTED with the reject reason. A retry with the same client_order_id
        returns the original order (200, Idempotent-Replayed: true)."""
        known(body.symbol)
        params = body.model_dump(exclude={"symbol", "client_order_id"})
        reply = await send(request(account, "new", symbol=body.symbol, key=body.client_order_id, params=params))
        if reply.replayed:
            response.status_code = 200
            response.headers["Idempotent-Replayed"] = "true"
            return await read_order(reply.order_id, account, reply.seq_no)  # type: ignore[arg-type]
        if reply.status == "rejected":
            raise rejected(reply)
        return order_from_events(reply.events, {"order_id": reply.order_id, "symbol": body.symbol,
                                                "client_order_id": body.client_order_id, "seq_no": reply.seq_no})

    @app.patch("/orders/{order_id}", response_model=OrderOut, tags=["orders"])
    async def modify(order_id: int, body: ReplaceIn, account: Trader,
                     idempotency_key: Annotated[str | None, Header()] = None) -> Any:
        """Cancel-replace: new price and total quantity (docs/order-types.md for priority)."""
        reply = await send(request(account, "replace", order_id=order_id, key=idempotency_key,
                                   params={"price": body.price, "qty": body.qty}))
        if reply.replayed:
            return await read_order(order_id, account, reply.seq_no)
        if reply.status == "rejected":
            raise rejected(reply)
        return order_from_events(reply.events, {"order_id": order_id, "symbol": reply.symbol, "seq_no": reply.seq_no})

    @app.delete("/orders/{order_id}", response_model=OrderOut, tags=["orders"])
    async def cancel(order_id: int, account: Trader,
                     idempotency_key: Annotated[str | None, Header()] = None) -> Any:
        reply = await send(request(account, "cancel", order_id=order_id, key=idempotency_key))
        if reply.replayed:
            return await read_order(order_id, account, reply.seq_no)
        if reply.status == "rejected":
            raise rejected(reply)
        return order_from_events(reply.events, {"order_id": order_id, "symbol": reply.symbol, "seq_no": reply.seq_no})

    @app.get("/orders/{order_id}", response_model=OrderOut, tags=["orders"])
    async def get_order(order_id: int, account: Trader, min_seq_no: int | None = None) -> Any:
        """From the projection. `min_seq_no` (a seq_no a write returned) waits until the
        projection has caught up with it: read-your-writes."""
        return await read_order(order_id, account, min_seq_no)

    # --- Market data and accounts -----------------------------------------------------------------

    @app.get("/book/{symbol}", tags=["market data"])
    async def get_book(symbol: str, depth: Annotated[int, Query(ge=1, le=50)] = 10) -> Any:
        """L2 snapshot from the matcher's cache, with the md_seq a WebSocket client continues from."""
        known(symbol)
        snap = await D().cache.get_book(symbol)
        if snap is None:
            raise ApiError(503, "BOOK_UNAVAILABLE", "no snapshot yet: is the matcher running?")
        return {**snap, "bids": snap["bids"][:depth], "asks": snap["asks"][:depth]}

    @app.get("/trades/{symbol}", tags=["market data"])
    async def get_trades(symbol: str, limit: Annotated[int, Query(ge=1, le=1000)] = 50) -> Any:
        known(symbol)
        return {"symbol": symbol, "trades": [{**t, "ts": ns_text(t.get("ts"))} for t in await D().store.trades(symbol, limit)]}

    @app.get("/accounts/me", tags=["accounts"])
    async def whoami(account: Trader) -> Any:
        """The key's own account (M11: a pasted key finds its account id)."""
        return {"account_id": account.account_id, "role": account.role, "practice": account.practice}

    @app.get("/accounts/{account_id}/positions", tags=["accounts"])
    async def get_positions(account_id: int, account: Trader, symbol: str | None = None,
                            min_seq_no: int | None = None) -> Any:
        """Position, cash (signed fill notional, ticks x qty), mark (last trade), PnL =
        cash + qty x mark, and open exposure. `symbol` + `min_seq_no` wait for the
        projection to reach that symbol's seq_no."""
        if account_id != account.account_id and account.role != "admin":
            raise ApiError(404, "NOT_FOUND", "no such account for this key")
        if min_seq_no is not None and symbol is not None:
            known(symbol)
            await D().store.wait_for(symbol, min_seq_no, settings.request_timeout_s)
        out = []
        for p in await D().store.positions(account_id):
            mark = p.get("mark")
            pnl = p["cash"] + p["qty"] * mark if mark is not None else None
            # Money aggregates are decimal strings (ticks x qty): they can outgrow 2**53.
            out.append({**p, "cash": str(p["cash"]), "open_notional": str(p["open_notional"]),
                        "pnl": str(pnl) if pnl is not None else None})
        return {"account_id": account_id, "positions": out}

    @app.get("/orders", tags=["orders"])
    async def list_orders(account: Trader, status: Literal["open", "all"] = "open", symbol: str | None = None,
                          limit: Annotated[int, Query(ge=1, le=500)] = 100, min_seq_no: int | None = None) -> Any:
        """The account's orders from the projection, newest first. `symbol` + `min_seq_no`
        wait for the projection to reach that seq_no."""
        known(symbol)
        if min_seq_no is not None and symbol is not None:
            await D().store.wait_for(symbol, min_seq_no, settings.request_timeout_s)
        rows = await D().store.list_orders(account.account_id, status == "open", symbol, limit)
        return {"orders": [OrderOut(**{k: r.get(k) for k in OrderOut.model_fields if k != "fills"}).model_dump()
                           for r in rows]}

    @app.get("/fills", tags=["orders"])
    async def list_fills(account: Trader, symbol: str | None = None,
                         limit: Annotated[int, Query(ge=1, le=500)] = 100, min_seq_no: int | None = None) -> Any:
        """The account's fills, newest first."""
        known(symbol)
        if min_seq_no is not None and symbol is not None:
            await D().store.wait_for(symbol, min_seq_no, settings.request_timeout_s)
        keep = ("symbol", "trade_id", "order_id", "side", "liquidity", "price", "qty", "leaves_qty", "cum_qty",
                "seq_no", "ts")
        rows = await D().store.fills(account.account_id, symbol, limit)
        return {"fills": [{**{k: f.get(k) for k in keep}, "ts": ns_text(f.get("ts"))} for f in rows]}

    # --- The dashboard: instruments, config, practice accounts (M11) ------------------------------

    instruments = {i.symbol: i for i in settings.instruments}

    @app.get("/instruments", tags=["market data"])
    async def get_instruments() -> Any:
        """Listed symbols with their tick size (decimal text) and display name. All fictional."""
        return {"instruments": [{"symbol": s, "tick_size": instruments[s].tick_size if s in instruments else "1",
                                 "name": instruments[s].name if s in instruments else ""}
                                for s in settings.symbols]}

    @app.get("/config", tags=["ops"])
    async def get_config() -> Any:
        """What the dashboard needs to know about this deployment."""
        return {"demo_mode": settings.demo_mode, "practice_backtest_cap": settings.practice_backtest_cap,
                "max_price_ticks": MAX_PRICE_TICKS, "max_order_qty": MAX_ORDER_QTY, "version": app.version}

    proxies = TrustedProxies(settings.trusted_proxies)

    @app.post("/practice-accounts", status_code=201, tags=["accounts"])
    async def create_practice_account(request: Request) -> Any:
        """Demo mode only: a new trader account with practice money, and its API key (shown
        once). Limited per client IP; behind the dashboard's nginx the client IP comes from
        X-Forwarded-For, believed only from a trusted proxy (services/api/proxies.py)."""
        if not settings.demo_mode:
            raise ApiError(404, "NOT_FOUND", "practice accounts exist only in demo mode")
        ip = await proxies.client_ip(request.client.host if request.client else None,
                                     request.headers.get("x-forwarded-for"))
        if await D().counter.incr(keys.practice(ip, int(time.time() // 3600)), 3600) \
                > settings.practice_per_ip_per_hour:
            raise ApiError(429, "PRACTICE_LIMIT", "too many practice accounts from this address: try again within "
                                                  "the hour")
        name = f"practice-{secrets.token_hex(4)}"
        row, key = new_account_row(name=name, role="trader")
        account_id = await D().store.create_account({**row, "practice": True})
        return {"account_id": account_id, "name": name, "role": "trader", "practice": True, "api_key": key}

    # --- Backtests ------------------------------------------------------------------------------

    # The committed showcase runs (research/showcase/<name>/metrics.json), read-only.
    showcase: dict[str, dict[str, Any]] = {}
    if settings.showcase_dir.is_dir():
        for d in sorted(settings.showcase_dir.iterdir()):
            if (d / "metrics.json").is_file() and (d / "record.json").is_file():
                showcase[f"showcase-{d.name}"] = {"dir": d}

    catalog_cache: dict[str, Any] = {"at": 0.0, "value": None}

    async def catalog() -> dict[str, Any] | None:
        if time.monotonic() - catalog_cache["at"] > 30 or catalog_cache["value"] is None:
            catalog_cache.update(at=time.monotonic(), value=await D().catalog())
        return catalog_cache["value"]  # type: ignore[no-any-return]

    async def known_symbols(config: dict[str, Any] | None) -> set[str]:
        """Every symbol a run could be about: the catalog's and the run's own."""
        names: set[str] = set()
        for ds in (await catalog() or {}).get("datasets", []):
            names.update(s["symbol"] for s in ds["symbols"])
        data = (config or {}).get("data") or {}
        names.update(data.get("symbols") or [])
        names.update((data.get("synthetic_bars") or {}).get("symbols") or [])
        return names

    def describe(config: dict[str, Any] | None) -> dict[str, Any]:
        """What a run was, in a few fields (the list page)."""
        cfg = config or {}
        data = cfg.get("data") or {}
        if "bars" in data:
            dataset = "US ETFs, daily (Tiingo, historical data)" if "tiingo" in str(data["bars"]) \
                else "Historical daily bars"
        elif "synthetic_bars" in data:
            dataset = "Synthetic daily bars"
        else:
            dataset = "Synthetic order flow" if "synthetic" in data else "Recorded order flow"
        strategy = (cfg.get("strategy") or {}).get("name")
        return {"strategy": strategy, "kind": cfg.get("kind"), "dataset": dataset, "real_data": "bars" in data,
                "costs": bool(cfg.get("costs")), "start": data.get("start"), "end": data.get("end"),
                "symbols": data.get("symbols") or (data.get("synthetic_bars") or {}).get("symbols")}

    async def backtest_view(run: dict[str, Any]) -> dict[str, Any]:
        view = {k: run.get(k) for k in ("id", "status", "attempts", "error", "seed", "commit", "created_at",
                                        "started_at", "finished_at")}
        try:
            view["metrics"] = public_metrics(run.get("metrics"), await known_symbols(run.get("config")))
        except NotPublishable as e:
            raise ApiError(500, "NOT_PUBLISHABLE", str(e)) from None
        view["config"] = stringify_big(run.get("config"))
        view["summary"] = describe(run.get("config"))
        view["featured"] = bool(run.get("featured"))
        view["tearsheet_url"] = f"/backtests/{run['id']}/tearsheet" if run.get("status") == "done" else None
        return view

    def showcase_run(run_id: str) -> dict[str, Any] | None:
        entry = showcase.get(run_id)
        if entry is None:
            return None
        if "run" not in entry:
            record = json.loads((entry["dir"] / "record.json").read_text())
            entry["run"] = {"id": run_id, "status": "done", "attempts": 1, "error": None, "seed": record.get("seed"),
                            "commit": (record.get("git") or {}).get("commit"), "created_at": None,
                            "started_at": None, "finished_at": None, "config": record.get("config"),
                            "metrics": json.loads((entry["dir"] / "metrics.json").read_text()), "featured": True}
        return entry["run"]  # type: ignore[no-any-return]

    async def any_run(run_id: str) -> dict[str, Any]:
        run = showcase_run(run_id) or await D().store.get_backtest(run_id)
        if run is None:
            raise ApiError(404, "NOT_FOUND", f"no backtest {run_id}")
        return run

    def headline(row: dict[str, Any]) -> dict[str, Any] | None:
        perf = (row.get("metrics") or {}).get("performance") if "metrics" in row else None
        sharpe = perf.get("sharpe") if perf else row.get("sharpe")
        if not sharpe:
            return None
        dsr = (perf or {}).get("dsr") if perf else row.get("dsr")
        ann = (perf or {}).get("annual_return") if perf else row.get("annual_return")
        return {"sharpe": sharpe, "dsr": dsr, "annual_return": ann,
                "period": (row.get("metrics") or {}).get("period") if "metrics" in row else row.get("period"),
                "trials": (row.get("metrics") or {}).get("trials") if "metrics" in row else row.get("trials")}

    @app.get("/backtests", tags=["backtests"])
    async def list_backtests(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> Any:
        """Public: every run on this server, newest first (headline metrics only), the
        featured showcase runs, and counts by status. Every run is recorded, failed ones too."""
        runs = []
        for r in await D().store.list_backtests(limit):
            runs.append({"id": r["id"], "status": r["status"], "created_at": r.get("created_at"),
                         "finished_at": r.get("finished_at"), "error": r.get("error"), "featured": False,
                         **describe(r.get("config")), "headline": stringify_big(headline(r))})
        featured = []
        for run_id in showcase:
            run = showcase_run(run_id)
            assert run is not None
            featured.append({"id": run_id, "status": "done", "featured": True, **describe(run["config"]),
                             "headline": stringify_big(headline(run))})
        return {"runs": runs, "featured": featured, "counts": await D().store.backtest_counts()}

    @app.get("/backtests/catalog", tags=["backtests"])
    async def get_catalog() -> Any:
        """Public: the datasets, strategies and limits the backtest form may choose from.
        Dates and symbols only, never a price."""
        cat = await catalog()
        if cat is None:
            raise ApiError(503, "CATALOG_UNAVAILABLE", "the backtest worker has not published its data catalog yet")
        return {"datasets": cat["datasets"],
                "strategies": {k: {"label": v["label"], "min_symbols": v["min_symbols"], "params": v["params"]}
                               for k, v in backtest_form.STRATEGIES.items()},
                "train_years": list(backtest_form.TRAIN_YEARS), "test_days": backtest_form.TEST_DAYS,
                "days_per_year": backtest_form.DAYS_PER_YEAR, "max_grid": backtest_form.MAX_GRID,
                "practice_backtest_cap": settings.practice_backtest_cap}

    @app.post("/backtests", status_code=202, tags=["backtests"])
    async def queue_backtest(body: BacktestIn, account: Trader) -> Any:
        """Queues a backtest: a `config` (the `make backtest` schema as JSON; the worker
        validates it fully, a bad one ends as status failed) or a dashboard `form`
        (validated here against the data catalog). Practice accounts: form only, and at
        most `practice_backtest_cap` runs queued or running at once."""
        if (body.config is None) == (body.form is None):
            raise ApiError(422, "VALIDATION_ERROR", "send exactly one of config and form")
        if body.form is not None:
            cat = await catalog()
            if cat is None:
                raise ApiError(503, "CATALOG_UNAVAILABLE", "the backtest worker has not published its data catalog yet")
            errors = backtest_form.validate(body.form, cat)
            if errors:
                raise ApiError(422, "VALIDATION_ERROR", "the backtest form is not valid",
                               details=[e.to_dict() for e in errors])
            cfg = backtest_form.build_config(body.form, cat)
        else:
            if account.practice:
                raise ApiError(403, "FORBIDDEN", "practice accounts queue backtests through the form")
            cfg = body.config  # type: ignore[assignment]
            if cfg.get("kind") not in ("event", "bars", "walkforward") or not isinstance(cfg.get("strategy"), dict) \
                    or not isinstance(cfg.get("data"), dict):
                raise ApiError(422, "VALIDATION_ERROR", "config needs kind (event|bars|walkforward), data and strategy")
        if len(json.dumps(cfg)) > 100_000:
            raise ApiError(422, "VALIDATION_ERROR", "config too large")
        run_id = uuid.uuid4().hex[:16]
        cap = settings.practice_backtest_cap if account.practice else None
        if not await D().store.create_backtest(run_id, account.account_id, cfg, cap):
            raise ApiError(429, "BACKTEST_LIMIT", f"a practice account can have {cap} backtests queued or running at "
                                                  f"once: wait for one to finish", limit=cap)
        await D().queue.enqueue(run_id)
        return {"id": run_id, "status": "queued"}

    @app.get("/backtests/{run_id}", tags=["backtests"])
    async def get_backtest(run_id: str) -> Any:
        """Public: status, config, a summary and the run's published metrics."""
        return await backtest_view(await any_run(run_id))

    @app.get("/backtests/{run_id}/tearsheet", tags=["backtests"], response_class=FileResponse)
    async def get_tearsheet(run_id: str) -> Any:
        """Public: the run's HTML tearsheet (rendered from its metrics alone)."""
        if run_id in showcase:
            path = showcase[run_id]["dir"] / "tearsheet.html"
        else:
            run = await any_run(run_id)
            path = settings.runs_dir / run_id / "tearsheet.html"
            if run["status"] != "done":
                path = settings.runs_dir / "-"  # nothing yet
        if not path.is_file():
            raise ApiError(404, "NOT_FOUND", "no tearsheet for this run yet")
        return FileResponse(path, media_type="text/html")

    # --- Admin ----------------------------------------------------------------------------------

    async def admin_command(account: Account, op: str, symbol: str | None, params: dict[str, Any]) -> Any:
        known(symbol)
        reply = await send(request(account, op, symbol=symbol, params=params))
        if reply.status == "rejected":
            raise rejected(reply, admin_cmd=True)
        return {"status": "ok", "results": reply.results}

    @app.post("/admin/risk-limits", tags=["admin"])
    async def set_limits(body: RiskLimitsIn, account: Admin) -> Any:
        """Instrument limits, default account limits, or one account's limits; journaled
        admin commands, in force from the next command (docs/risk.md)."""
        allowed = ("max_qty", "band_bps", "reference_price") if body.scope == "instrument" else (
            "max_notional", "position_limit", "credit", "max_open_orders", "rate_per_sec", "rate_burst")
        unknown = set(body.limits) - set(allowed)
        if unknown:
            raise ApiError(422, "VALIDATION_ERROR", f"unknown limits for scope {body.scope}: {sorted(unknown)}")
        if (body.scope == "account") != (body.account_id is not None):
            raise ApiError(422, "VALIDATION_ERROR", "account_id is required for scope account, and only there")
        if body.scope == "instrument":
            return await admin_command(account, "instrument_limits", body.symbol, dict(body.limits))
        return await admin_command(account, "account_limits", body.symbol,
                                   {**body.limits, "target_account": body.account_id})

    @app.post("/admin/kill-switch", tags=["admin"])
    async def kill_switch(body: KillSwitchIn, account: Admin) -> Any:
        return await admin_command(account, "kill_switch", body.symbol,
                                   {"engaged": body.engaged, "target_account": body.account_id,
                                    "cancel_open": body.cancel_open})

    @app.post("/admin/session", tags=["admin"])
    async def set_session(body: SessionIn, account: Admin) -> Any:
        return await admin_command(account, "session", body.symbol, {"state": body.state})

    # --- Health and metrics -----------------------------------------------------------------------

    @app.get("/health", tags=["ops"])
    async def health() -> Any:
        checks = await D().health()
        ok = all(v == "up" for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", **checks})

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            from prometheus_client import multiprocess

            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # --- WebSockets -----------------------------------------------------------------------------

    def subscriber(initial_snapshot: bool) -> Subscriber:
        return Subscriber(settings.ws_queue, max_overflows=settings.ws_max_overflows,
                          window_s=settings.ws_overflow_window_s, initial_snapshot=initial_snapshot)

    async def pump(ws: WebSocket, sub: Subscriber, handle: Any, on_text: Any = None) -> None:
        """Runs the writer until it stops or the client goes away."""
        async def reader() -> None:
            try:
                while True:
                    text = await ws.receive_text()
                    if on_text is not None:
                        on_text(text)
            except WebSocketDisconnect:
                pass

        async def writer() -> None:
            while True:
                item = await sub.get()
                if item is CLOSED:
                    await ws.close(WS_SLOW, "slow consumer: reconnect and resnapshot")
                    return
                if await handle(item) is False:
                    return

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()

    async def send_json(ws: WebSocket, sub: Subscriber, msg: Any) -> None:
        hit = find_unsafe(msg)
        if hit:  # never let a JavaScript client round an integer silently
            log.error("refusing to send an unsafe integer at %s over WebSocket", hit)
            sub.close("unsafe integer")
            await ws.close(WS_INTERNAL, f"unsafe integer at {hit}")
            raise RuntimeError("unsafe integer")
        try:
            await asyncio.wait_for(ws.send_text(json.dumps(msg)), settings.ws_send_timeout_s)
        except TimeoutError:
            sub.close("send timed out")
            raise

    async def ws_account(ws: WebSocket) -> Account | None:
        account = await lookup(ws.headers.get("x-api-key"))
        if account is None:  # browsers cannot set headers: the first message may carry the key
            try:
                first = json.loads(await asyncio.wait_for(ws.receive_text(), 2.0))
                account = await lookup(first.get("api_key") if isinstance(first, dict) else None)
            except (TimeoutError, ValueError, WebSocketDisconnect):
                account = None
        return account

    @app.websocket("/ws/marketdata/{symbol}")
    async def ws_marketdata(ws: WebSocket, symbol: str) -> None:
        """A snapshot, then updates numbered by md_seq. A gap or a new epoch means take a new
        snapshot (reconnect); after a slow-consumer overflow the server sends
        {"type": "resnapshot"} and a fresh snapshot by itself; repeated overflows close
        the connection with code 4008."""
        await ws.accept()
        if symbol not in symbols:
            await ws.close(WS_NOT_FOUND, "unknown symbol")
            return
        hub, sub, channel = D().hub, subscriber(True), keys.md_channel(symbol)
        hub.subscribe(channel, sub)
        state = {"epoch": None, "md_seq": -1, "first": True}

        def on_text(text: str) -> None:
            """{"type": "ping", "id": n} -> {"type": "pong", "id": n}, through the client's
            own queue: the dashboard's round-trip time includes any queueing."""
            try:
                msg = json.loads(text)
            except ValueError:
                return
            if isinstance(msg, dict) and msg.get("type") == "ping":
                ping_id = msg.get("id")
                if isinstance(ping_id, int) and not isinstance(ping_id, bool) and 0 <= ping_id <= MAX_SAFE:
                    sub.offer({"type": "pong", "id": ping_id})

        async def handle(item: Any) -> bool:
            if item is RESNAPSHOT:
                snap = await D().cache.get_book(symbol)
                if snap is None:
                    await asyncio.sleep(0.1)
                    sub._needs_snapshot = True
                    return True
                if not state["first"]:
                    WS_RESNAPSHOTS.inc()
                    await send_json(ws, sub, {"type": "resnapshot", "symbol": symbol})
                state.update(first=False, epoch=snap["epoch"], md_seq=snap["md_seq"])
                await send_json(ws, sub, snap)
                return True
            if item.get("type") == "pong":
                await send_json(ws, sub, item)
                return True
            if item.get("epoch") == state["epoch"] and item.get("md_seq", 0) <= state["md_seq"]:
                return True  # already in the snapshot sent
            if item.get("type") == "snapshot":  # a matcher restart: a new epoch
                state.update(epoch=item["epoch"], md_seq=item["md_seq"])
            await send_json(ws, sub, item)
            return True

        try:
            await pump(ws, sub, handle, on_text)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(channel, sub)
            if sub.closed and sub.close_reason != "unsafe integer":
                WS_SLOW_CLOSED.labels("marketdata").inc()
            if sub.closed and sub.close_reason != "unsafe integer" \
                    and ws.application_state == WebSocketState.CONNECTED:
                # Queued behind whatever the client has not read yet. If it never reads
                # again, the server's ping timeout drops the connection.
                try:
                    await ws.close(WS_SLOW, f"{sub.close_reason}: reconnect and resnapshot")
                except (RuntimeError, WebSocketDisconnect):
                    pass

    @app.websocket("/ws/executions")
    async def ws_executions(ws: WebSocket) -> None:
        """Execution reports for the key's account. No snapshot exists for these: after an
        overflow the connection closes (4008) and the client resyncs over REST."""
        await ws.accept()
        account = await ws_account(ws)
        if account is None:
            await ws.close(WS_UNAUTHORIZED, "a valid API key is required")
            return
        hub, sub, channel = D().hub, subscriber(False), keys.exec_channel(account.account_id)
        hub.subscribe(channel, sub)
        await ws.send_text(json.dumps({"type": "subscribed", "account_id": account.account_id}))

        async def handle(item: Any) -> bool:
            if item is RESNAPSHOT:
                sub.close("slow consumer")
                await ws.close(WS_SLOW, "slow consumer: resync over REST")
                return False
            await send_json(ws, sub, item)
            return True

        try:
            await pump(ws, sub, handle)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(channel, sub)

    @app.websocket("/ws/backtests/{run_id}")
    async def ws_backtest(ws: WebSocket, run_id: str) -> None:
        """Public (M11): the run's status, its progress stages, then its equity curve and
        done/failed."""
        await ws.accept()
        hub, sub, channel = D().hub, subscriber(False), keys.backtest_channel(run_id)
        hub.subscribe(channel, sub)  # before reading the status, so nothing falls in between
        try:
            run = showcase_run(run_id) or await D().store.get_backtest(run_id)
            if run is None:
                await ws.close(WS_NOT_FOUND, "no such backtest")
                return
            await send_json(ws, sub, {"type": "status", **await backtest_view(run)})
            if run["status"] in ("done", "failed"):
                if run["status"] == "done" and not run.get("featured") and hasattr(D().store, "equity"):
                    points = await D().store.equity(run_id)  # type: ignore[attr-defined]
                    await ws.send_text(json.dumps({"type": "equity", "points": points}))
                await ws.send_text(json.dumps({"type": run["status"], "id": run_id}))
                await ws.close()
                return

            async def handle(item: Any) -> bool:
                if item is RESNAPSHOT:
                    return True
                await send_json(ws, sub, item)
                if item.get("type") in ("done", "failed"):
                    await ws.close()
                    return False
                return True

            await pump(ws, sub, handle)
        except (TimeoutError, WebSocketDisconnect, RuntimeError, ApiError):
            pass
        finally:
            hub.unsubscribe(channel, sub)

    return app
