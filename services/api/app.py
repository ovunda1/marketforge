"""The gateway (M10): FastAPI REST and WebSocket in front of the single matcher.

Run with several worker processes (`uvicorn --factory services.api.app:create_app
--workers N`). No worker holds an engine: order entry and admin commands go to the
matcher over Redis (services/api/real.py); reads come from the Postgres projection and
the Redis book cache; WebSocket clients are fed from Redis pub/sub through the `Hub`.

Errors share one schema: ``{"error": {"code", "message", "request_id", ...}}``, with
`reason` holding the engine's reject code when the engine rejected a command.
"""

# No `from __future__ import annotations`: FastAPI reads the endpoint annotations at
# runtime, and the dependency aliases below are local to create_app.
import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocketDisconnect, WebSocketState

from services.api.auth import split_api_key, verify_api_key
from services.api.deps import Account, Deps, MatcherTimeout
from services.api.hub import CLOSED, RESNAPSHOT, Subscriber
from services.common import keys
from services.common.config import Settings
from services.common.protocol import Reply
from services.common.protocol import Request as MatcherRequest

HTTP_REQUESTS = Counter("mf_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_SECONDS = Histogram("mf_http_request_seconds", "HTTP request latency", ["method", "route"])
WS_SLOW_CLOSED = Counter("mf_ws_slow_consumers_total", "WebSocket clients closed as slow consumers", ["channel"])
WS_RESNAPSHOTS = Counter("mf_ws_resnapshots_total", "Snapshots resent after a slow-consumer overflow")

ERROR_STATUS = {"IDEMPOTENCY_CONFLICT": 409, "UNKNOWN_SYMBOL": 404, "NOT_FOUND": 404, "SYMBOL_HALTED": 503,
                "ENGINE_FAILED": 503, "EXPIRED": 504, "BAD_REQUEST": 400}
WS_UNAUTHORIZED, WS_NOT_FOUND, WS_SLOW = 4401, 4404, 4008


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


# --- Models -------------------------------------------------------------------------------------

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderIn(Strict):
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["limit", "market"] = "limit"
    tif: Literal["gtc", "ioc", "fok", "day"] = "gtc"
    price: StrictInt = 0  # ticks; a float is a validation error, never rounded
    qty: StrictInt
    post_only: StrictBool = False
    stp: Literal["cancel_newest", "cancel_oldest", "cancel_both"] = "cancel_newest"
    client_order_id: str = Field(min_length=1, max_length=64)


class ReplaceIn(Strict):
    price: StrictInt
    qty: StrictInt


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
    config: dict[str, Any]


# --- Helpers ------------------------------------------------------------------------------------

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
            o.update(status="cancelled", leaves_qty=0, cancelled_qty=e["cancelled_qty"])
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

    app = FastAPI(title="MarketForge", version="0.10.0", lifespan=lifespan,
                  description="Simulated exchange: REST and WebSocket gateway in front of a single matcher. "
                              "Prices are integer ticks, quantities integer units.")
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

    async def authenticated(x_api_key: Annotated[str | None, Header()] = None) -> Account:
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
        return {"symbol": symbol, "trades": await D().store.trades(symbol, limit)}

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
            out.append({**p, "pnl": p["cash"] + p["qty"] * mark if mark is not None else None})
        return {"account_id": account_id, "positions": out}

    # --- Backtests ------------------------------------------------------------------------------

    def backtest_view(run: dict[str, Any]) -> dict[str, Any]:
        view = {k: run.get(k) for k in ("id", "status", "attempts", "error", "seed", "commit", "created_at",
                                        "started_at", "finished_at", "metrics")}
        view["tearsheet_url"] = f"/backtests/{run['id']}/tearsheet" if run.get("status") == "done" else None
        return view

    async def own_run(run_id: str, account: Account) -> dict[str, Any]:
        run = await D().store.get_backtest(run_id)
        if run is None or (run["account_id"] != account.account_id and account.role != "admin"):
            raise ApiError(404, "NOT_FOUND", f"no backtest {run_id} for this account")
        return run

    @app.post("/backtests", status_code=202, tags=["backtests"])
    async def queue_backtest(body: BacktestIn, account: Trader) -> Any:
        """Queues a backtest config (the same TOML schema as `make backtest`, as JSON). The
        worker validates it fully; a bad config ends as status failed with the error."""
        cfg = body.config
        if cfg.get("kind") not in ("event", "bars", "walkforward") or not isinstance(cfg.get("strategy"), dict) \
                or not isinstance(cfg.get("data"), dict):
            raise ApiError(422, "VALIDATION_ERROR", "config needs kind (event|bars|walkforward), data and strategy")
        if len(json.dumps(cfg)) > 100_000:
            raise ApiError(422, "VALIDATION_ERROR", "config too large")
        run_id = uuid.uuid4().hex[:16]
        await D().store.create_backtest(run_id, account.account_id, cfg)
        await D().queue.enqueue(run_id)
        return {"id": run_id, "status": "queued"}

    @app.get("/backtests/{run_id}", tags=["backtests"])
    async def get_backtest(run_id: str, account: Trader) -> Any:
        return backtest_view(await own_run(run_id, account))

    @app.get("/backtests/{run_id}/tearsheet", tags=["backtests"], response_class=FileResponse)
    async def get_tearsheet(run_id: str, account: Trader) -> Any:
        run = await own_run(run_id, account)
        path = settings.runs_dir / run_id / "tearsheet.html"
        if run["status"] != "done" or not path.is_file():
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

    async def pump(ws: WebSocket, sub: Subscriber, handle: Any) -> None:
        """Runs the writer until it stops or the client goes away."""
        async def reader() -> None:
            try:
                while True:
                    await ws.receive_text()
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
            if item.get("epoch") == state["epoch"] and item.get("md_seq", 0) <= state["md_seq"]:
                return True  # already in the snapshot sent
            if item.get("type") == "snapshot":  # a matcher restart: a new epoch
                state.update(epoch=item["epoch"], md_seq=item["md_seq"])
            await send_json(ws, sub, item)
            return True

        try:
            await pump(ws, sub, handle)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(channel, sub)
            if sub.closed:
                WS_SLOW_CLOSED.labels("marketdata").inc()
            if sub.closed and ws.application_state == WebSocketState.CONNECTED:
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
        """The run's status, its progress stages, then its equity curve and done/failed."""
        await ws.accept()
        account = await ws_account(ws)
        if account is None:
            await ws.close(WS_UNAUTHORIZED, "a valid API key is required")
            return
        hub, sub, channel = D().hub, subscriber(False), keys.backtest_channel(run_id)
        hub.subscribe(channel, sub)  # before reading the status, so nothing falls in between
        try:
            run = await D().store.get_backtest(run_id)
            if run is None or (run["account_id"] != account.account_id and account.role != "admin"):
                await ws.close(WS_NOT_FOUND, "no such backtest")
                return
            await ws.send_text(json.dumps({"type": "status", **backtest_view(run)}))
            if run["status"] in ("done", "failed"):
                if run["status"] == "done" and hasattr(D().store, "equity"):
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
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unsubscribe(channel, sub)

    return app
