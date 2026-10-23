# Services (M10)

The exchange as running processes: a FastAPI gateway, a single matcher, a Postgres
projector and a backtest worker, with Redis and PostgreSQL, brought up by docker
compose. The design and its reasons are in `docs/learning/m10.md`; this page is the
reference.

```sh
cp .env.example .env        # set POSTGRES_PASSWORD; .env is git-ignored
make up                     # build the image, start everything, wait until healthy
docker compose --env-file .env -f deploy/docker-compose.yml exec -T api \
  python -m services.api.accounts create --name alice          # prints the API key once
make gateway-smoke          # REST round trip and WebSocket timing, one JSON line
make projection-rebuild     # forget Postgres's projection; the projector rebuilds it
make down
make test-services          # the API suite on a separate, fresh stack (project mf-test)
```

The OpenAPI document is served at `/docs` and `/openapi.json`.

## Processes

| Service | Command | Owns | Notes |
| --- | --- | --- | --- |
| `matcher` | `python -m services.matcher` | every symbol's `Engine`, sequencer, journal, intake log, snapshots | exactly one: compose runs one replica and the data-directory lock (`flock` on `LOCK`) refuses a second |
| `api` | `uvicorn --factory services.api.app:create_app --workers 2` | nothing durable | never imports `mfengine` (a test checks) |
| `projector` | `python -m services.projector run` | the projection tables and their cursors | reads the journals (read-only mount), never Redis |
| `worker` | `arq services.worker.tasks.WorkerSettings` | backtest outputs, backtest tables | the only service that gets `TIINGO_API_KEY` |
| `migrate` | `python -m services.db.migrate` | schema, instrument seed | one-shot, before the others |

Files of the matcher (volume `data`, `MF_DATA_DIR`), per symbol:
`<SYM>/journal.mfj` (docs/journal-format.md), `<SYM>/intake.jsonl`,
`<SYM>/snapshots/snap-<seq_no>.mfsnap` (newest three kept).

## Configuration

Environment only; secrets have no defaults. Instruments: `deploy/config/instruments.yaml`
(`MF_INSTRUMENTS`), each with an optional risk config (docs/risk.md) journaled at the
symbol's first start.

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

## REST

Every request except `/book`, `/trades`, `/health`, `/metrics` and the docs needs
`X-API-Key`. Prices are integer ticks, quantities integer units; a float price is a
validation error, never rounded.

| Method | Path | Result |
| --- | --- | --- |
| POST | `/orders` | 201 with the order's state after the command (status, fills, `seq_no`); 422 `ORDER_REJECTED` with the engine's `reason`; 200 + `Idempotent-Replayed: true` for a retried `client_order_id` |
| PATCH | `/orders/{id}` | cancel-replace (`price`, total `qty`); optional `Idempotency-Key` header |
| DELETE | `/orders/{id}` | cancel; 409 `ORDER_REJECTED` reason `UNKNOWN_ORDER` if it is no longer open |
| GET | `/orders/{id}?min_seq_no=` | from the projection; `min_seq_no` waits for it (read-your-writes) |
| GET | `/book/{symbol}?depth=10` | L2 from the matcher's cache, with `epoch` and `md_seq` |
| GET | `/trades/{symbol}?limit=50` | newest first |
| GET | `/accounts/{id}/positions?symbol=&min_seq_no=` | qty, cash (signed fill notional), mark, PnL = cash + qty x mark, open exposure |
| POST | `/backtests` | 202 `{id}`; the config is the `make backtest` schema as JSON |
| GET | `/backtests/{id}`, `/backtests/{id}/tearsheet` | status, attempts, metrics; the HTML tearsheet |
| POST | `/admin/risk-limits`, `/admin/kill-switch`, `/admin/session` | admin role; journaled admin commands; `symbol` omitted = every symbol |
| GET | `/health`, `/metrics` | 503 `degraded` without a matcher heartbeat, Redis or Postgres; Prometheus |

Errors: `{"error": {"code", "message", "request_id", ...}}`. Codes: `UNAUTHORIZED` 401,
`FORBIDDEN` 403, `NOT_FOUND`/`UNKNOWN_SYMBOL` 404, `IDEMPOTENCY_CONFLICT` 409,
`VALIDATION_ERROR` 422, `ORDER_REJECTED`/`ADMIN_REJECTED` 422 (409 for
`UNKNOWN_ORDER`), `RATE_LIMITED` 429, `SYMBOL_HALTED` 503, `MATCHER_TIMEOUT` 504 (the
outcome is unknown: retry with the same key, or GET).

## WebSocket

- `/ws/marketdata/{symbol}` (public). First a `snapshot` (`epoch`, `md_seq`, `seq_no`,
  `bids`/`asks` as `[price, qty, orders]`), then `update` messages with `md_seq` +1
  each, the levels that changed in the top `MF_DEPTH` (qty 0 removes), the trades and
  `l1`. A gap or a new `epoch` (the matcher restarted) means reconnect for a new snapshot.
  If a client falls behind its queue, the server sends `{"type": "resnapshot"}` and a
  fresh snapshot; repeated overflows, or a send blocked longer than the send timeout,
  close it with code 4008. `services/client.py` (`BookClient`) implements the client side.
- `/ws/executions` (key in the `X-API-Key` header, or `{"api_key": ...}` as the first
  message within 2 s). Execution reports for that account only. Overflow closes with
  4008: resync over REST.
- `/ws/backtests/{id}` (key as above): `status`, `progress` stages, `equity` chunks, then
  `done` or `failed`.

Close codes: 4401 no valid key, 4404 unknown symbol or run, 4008 slow consumer. A client
that stops reading entirely may instead be dropped by uvicorn's keepalive ping timeout
(1011) before the queued 4008 reaches it. `mf_ws_slow_consumers_total` and
`mf_ws_resnapshots_total` on `/metrics` count what the gateway did.
