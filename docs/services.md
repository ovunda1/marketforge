# Services (M10, M11)

The exchange as running processes: a FastAPI gateway, a single matcher, a Postgres
projector and a backtest worker, with Redis and PostgreSQL, brought up by docker
compose; since M11 also the dashboard (nginx) and a demo liquidity bot. The design and
its reasons are in `docs/learning/m10.md` and `docs/learning/m11.md`; the dashboard
itself is in `docs/dashboard.md`; this page is the reference.

```sh
cp .env.example .env        # set POSTGRES_PASSWORD; .env is git-ignored
make up                     # build the images, start everything, wait until healthy
                            # the dashboard: http://127.0.0.1:8080
docker compose --env-file .env -f deploy/docker-compose.yml exec -T api \
  python -m services.api.accounts create --name alice          # prints the API key once
make gateway-smoke          # REST round trip and WebSocket timing, one JSON line
make projection-rebuild     # forget Postgres's projection; the projector rebuilds it
make down
make test-services          # the API suite on a separate, fresh stack (project mf-test)
make e2e                    # the dashboard's browser tests, on a fresh test stack
```

The OpenAPI document is served at `/docs` and `/openapi.json` (through the dashboard:
`/api/docs`). The key is declared as an `apiKey` security scheme (`X-API-Key`), so the docs
page has an Authorize button; public routes carry no security requirement.

## Processes

| Service | Command | Owns | Notes |
| --- | --- | --- | --- |
| `matcher` | `python -m services.matcher` | every symbol's `Engine`, sequencer, journal, intake log, snapshots | exactly one: compose runs one replica and the data-directory lock (`flock` on `LOCK`) refuses a second |
| `api` | `uvicorn --factory services.api.app:create_app --workers 2` | nothing durable | never imports `mfengine` (a test checks) |
| `projector` | `python -m services.projector run` | the projection tables and their cursors | reads the journals (read-only mount), never Redis |
| `worker` | `arq services.worker.tasks.WorkerSettings` | backtest outputs, backtest tables | the only service that gets `TIINGO_API_KEY` |
| `migrate` | `python -m services.db.migrate` | schema, instrument seed | one-shot, before the others |
| `dashboard` | nginx (`deploy/dashboard.Dockerfile`, `deploy/nginx.conf`) | nothing | the static site; `/api/*` (prefix stripped) and `/ws/*` proxied to `api`; overwrites `X-Forwarded-For` |
| `demo-bot` | `python -m services.demo` | its two accounts' keys (volume `bot`) | quotes ACME, GLOBEX, INITECH through the public REST API (`services/demo/bot.py`); compose profile `demo`: started by `make up` and `make e2e`, not by `make test-services`, whose projection checks need a quiet exchange |

Files of the matcher (volume `data`, `MF_DATA_DIR`), per symbol:
`<SYM>/journal.mfj` (docs/journal-format.md), `<SYM>/intake.jsonl`,
`<SYM>/snapshots/snap-<seq_no>.mfsnap` (newest three kept).

## Configuration

Environment only; secrets have no defaults. Instruments: `deploy/config/instruments.yaml`
(`MF_INSTRUMENTS`), each with an optional display `name` and risk config (docs/risk.md)
journaled at the symbol's first start. Every listed symbol is fictional (ACME, GLOBEX,
INITECH, and SMOKE for `make gateway-smoke`); a test checks the list against real tickers.

| Variable | Default | Used by |
| --- | --- | --- |
| `PGPASSWORD` | (required, from `POSTGRES_PASSWORD` in `.env`) | api, projector, worker, migrate |
| `PGHOST`, `PGUSER`, `PGDATABASE`, `PGPORT` | `localhost`, `marketforge`, `marketforge`, `5432` | same |
| `MF_REDIS_URL` | `redis://localhost:6379/0` | all |
| `MF_REQUEST_TIMEOUT_S` | `2.0` | api: wait for the matcher; also the request deadline |
| `MF_FSYNC` | `1` | matcher: group commit (0 only for experiments) |
| `MF_SNAPSHOT_EVERY` | `10000` | matcher: commands per symbol between snapshots |
| `MF_DEPTH` | `50` | matcher: levels per side in market data |
| `MF_WS_QUEUE`, `MF_WS_MAX_OVERFLOWS`, `MF_WS_OVERFLOW_WINDOW_S`, `MF_WS_SEND_TIMEOUT_S` | `1000`, `3`, `30`, `5` | api: slow-consumer policy |
| `MF_RATE_LIMIT_PER_S` | `2000` | api: requests per account per second |
| `MF_BACKTEST_TIMEOUT_S` | `1800` | worker: a longer run is marked failed; a killed worker's job is rerun after timeout + 70 s (arq's lock) |
| `TIINGO_API_KEY` | empty | worker only |
| `MF_DEMO_MODE` | `0` (`1` in `docker-compose.yml`) | api: `POST /practice-accounts` exists only when `1` |
| `MF_TRUSTED_PROXIES` | empty (`dashboard` in compose) | api: peers (IP, CIDR, hostname) whose `X-Forwarded-For` is believed |
| `MF_PRACTICE_PER_IP_PER_HOUR` | `5` | api: practice accounts per client IP per hour |
| `MF_PRACTICE_BACKTESTS` | `2` | api: queued + running backtests per practice account |
| `MF_SHOWCASE_DIR` | `research/showcase` | api: the committed showcase runs, served as featured runs |
| `MF_DATA_ROOT` | `data/normalized` | worker: datasets for the catalog (compose mounts the host's, read-only) |
| `MF_API_URL`, `MF_BOT_SYMBOLS`, `MF_BOT_SEED`, `MF_BOT_INTERVAL_S`, `MF_BOT_STATE` | `http://api:8000`, `ACME:4200,GLOBEX:1850,INITECH:640`, `1`, `0.5`, `/var/lib/marketforge/bot/keys.json` | demo-bot |

## REST

Public (no key): `/book`, `/trades`, `/instruments`, `/config`, `/health`, `/metrics`, the
docs, `POST /practice-accounts` (demo mode), and every backtest read (`GET /backtests`,
`/backtests/catalog`, `/backtests/{id}`, its tearsheet). Everything else needs `X-API-Key`.
Prices are integer ticks, quantities integer units; a float price is a validation error,
never rounded.

Integers and JavaScript (M11, `services/common/safe_json.py`): a browser rounds integers
beyond 2**53 - 1. Per-order prices and quantities are capped at the gateway (10^9 ticks,
10^9 units), so ids, prices and quantities stay far inside the range; money aggregates
(position `cash`, `pnl`, `open_notional`) and journal timestamps (`ts`, nanoseconds) are
decimal strings; research outputs (metrics, configs) turn any larger integer into a string.
A response that still holds an unsafe integer is refused (500 `UNSAFE_INTEGER`; a WebSocket
closes with 1011) rather than sent.

| Method | Path | Result |
| --- | --- | --- |
| POST | `/orders` | 201 with the order's state after the command (status, fills, `seq_no`); 422 `ORDER_REJECTED` with the engine's `reason`; 200 + `Idempotent-Replayed: true` for a retried `client_order_id` |
| PATCH | `/orders/{id}` | cancel-replace (`price`, total `qty`); optional `Idempotency-Key` header |
| DELETE | `/orders/{id}` | cancel; 409 `ORDER_REJECTED` reason `UNKNOWN_ORDER` if it is no longer open |
| GET | `/orders/{id}?min_seq_no=` | from the projection; `min_seq_no` waits for it (read-your-writes) |
| GET | `/orders?status=open\|all&symbol=&limit=&min_seq_no=` | the key's orders, newest first (M11) |
| GET | `/fills?symbol=&limit=&min_seq_no=` | the key's fills, newest first (M11) |
| GET | `/accounts/me` | the key's account id, role, practice flag (M11) |
| GET | `/instruments` | symbol, tick size (text), display name (M11) |
| GET | `/config` | demo mode, the practice backtest cap, input bounds (M11) |
| POST | `/practice-accounts` | demo mode only: 201 with a new trader account's key (shown once); 429 `PRACTICE_LIMIT` past the per-IP limit (M11) |
| GET | `/book/{symbol}?depth=10` | L2 from the matcher's cache, with `epoch` and `md_seq` |
| GET | `/trades/{symbol}?limit=50` | newest first |
| GET | `/accounts/{id}/positions?symbol=&min_seq_no=` | qty, cash (signed fill notional, text), mark, PnL = cash + qty x mark (text), open exposure |
| POST | `/backtests` | 202 `{id}`; either `config` (the `make backtest` schema as JSON) or `form` (the dashboard's, validated against the catalog: 422 with `details: [{field, message}]`). Practice accounts: form only (403), at most 2 queued or running (429 `BACKTEST_LIMIT`) |
| GET | `/backtests` | public: every run on this server (headline metrics), the featured showcase runs, counts by status (failed included) |
| GET | `/backtests/catalog` | public: datasets (symbols with first/last dates, the trading calendar, never a price), strategies and their allowed parameters; 503 `CATALOG_UNAVAILABLE` until the worker has published it |
| GET | `/backtests/{id}`, `/backtests/{id}/tearsheet` | public: status, config, summary, metrics (checked portfolio-level: 500 `NOT_PUBLISHABLE` otherwise); the HTML tearsheet. `showcase-tsmom` and `showcase-xsmom` are the committed showcase runs |
| POST | `/admin/risk-limits`, `/admin/kill-switch`, `/admin/session` | admin role; journaled admin commands; `symbol` omitted = every symbol |
| GET | `/health`, `/metrics` | 503 `degraded` without a matcher heartbeat, Redis or Postgres; Prometheus |

Errors: `{"error": {"code", "message", "request_id", ...}}`. Codes: `UNAUTHORIZED` 401,
`FORBIDDEN` 403, `NOT_FOUND`/`UNKNOWN_SYMBOL` 404, `IDEMPOTENCY_CONFLICT` 409,
`VALIDATION_ERROR` 422, `ORDER_REJECTED`/`ADMIN_REJECTED` 422 (409 for
`UNKNOWN_ORDER`), `RATE_LIMITED` 429, `SYMBOL_HALTED` 503, `MATCHER_TIMEOUT` 504 (the
outcome is unknown: retry with the same key, or GET). M11: `PRACTICE_LIMIT` and
`BACKTEST_LIMIT` 429, `CATALOG_UNAVAILABLE` 503, `NOT_PUBLISHABLE` and `UNSAFE_INTEGER`
500. The full list is `ERROR_CODES` in `services/api/app.py`; the dashboard has a message
and a suggested fix for each one and for every engine reject reason
(`dashboard/src/content/rejects.json`, checked by a test). An order's `cancel_reason`
(user, unfilled, self_trade, expired, kill_switch) says why it was cancelled.

The client IP (M11, `services/api/proxies.py`): uvicorn runs with `--no-proxy-headers`;
`X-Forwarded-For` is believed only from a `MF_TRUSTED_PROXIES` peer (the dashboard's nginx,
which overwrites the header), and then only its last entry. Anyone else's header is
ignored, so the per-IP practice limit cannot be dodged by inventing one.

## WebSocket

- `/ws/marketdata/{symbol}` (public). First a `snapshot` (`epoch`, `md_seq`, `seq_no`,
  `bids`/`asks` as `[price, qty, orders]`), then `update` messages with `md_seq` +1
  each, the levels that changed in the top `MF_DEPTH` (qty 0 removes), the trades and
  `l1`. A gap or a new `epoch` (the matcher restarted) means reconnect for a new snapshot.
  If a client falls behind its queue, the server sends `{"type": "resnapshot"}` and a
  fresh snapshot; repeated overflows, or a send blocked longer than the send timeout,
  close it with code 4008. `services/client.py` (`BookClient`) implements the client side;
  the dashboard's `dashboard/src/lib/bookClient.ts` is a port, and both run the same
  fixtures (`tests/fixtures/marketdata/cases.json`, generated from a real matcher). A
  client may send `{"type": "ping", "id": n}`; the answer `{"type": "pong", "id": n}` goes
  through its own queue (M11: the dashboard's round-trip time).
- `/ws/executions` (key in the `X-API-Key` header, or `{"api_key": ...}` as the first
  message within 2 s). Execution reports for that account only. Overflow closes with
  4008: resync over REST.
- `/ws/backtests/{id}` (public since M11): `status`, `progress` stages, `equity` chunks,
  then `done` or `failed`.

Close codes: 4401 no valid key, 4404 unknown symbol or run, 4008 slow consumer. A client
that stops reading entirely may instead be dropped by uvicorn's keepalive ping timeout
(1011) before the queued 4008 reaches it. `mf_ws_slow_consumers_total` and
`mf_ws_resnapshots_total` on `/metrics` count what the gateway did.
