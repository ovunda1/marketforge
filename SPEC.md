# MarketForge — Build Spec for Claude Code

Oct 5, 2026 · @Ovunda

## Overview

MarketForge is a simulated exchange plus a research backtester: a C++ matching engine at the core, a Python backtesting and analytics layer on top, and FastAPI services that expose both over REST and WebSocket. The bar for "done" is that every resume claim maps to code, a test, and a measured number you can defend in a quant or SWE interview.

**Goals**

- **Correct:** the engine passes unit, golden-file and randomized differential tests against a simple reference engine.
- **Deterministic:** replaying the same input journal produces a byte-identical output event stream.
- **Measured:** published throughput (orders/sec) and latency percentiles (p50, p99, p99.9), with machine spec and compiler flags.
- **Realistic:** backtests report gross and net performance, net of fees, spread, slippage, market impact and latency.
- **Runnable:** `make up` starts everything in Docker; `make test` and `make bench` reproduce every number in the README.

**Non-goals (v1):** real-money or live-broker trading, FIX protocol, distributed matching across machines, multi-asset margining. FIX and an opening auction are stretch goals.

| Resume claim | Where it lives | Proof |
| --- | --- | --- |
| Price-time priority, partial fills, cancels | `engine/` order book | Unit + differential tests |
| Pre-trade risk controls | `engine/risk/` | Reject-reason tests, risk latency benchmark |
| Deterministic market replay | Journal + replay tool | Output-hash equality test |
| Momentum backtesting | `research/strategies/` | Example tearsheet in README |
| Transaction-cost and slippage modeling | `research/costs/` | Gross vs net comparison |
| REST/WebSocket services | `services/api/` | API tests, OpenAPI docs |
| Throughput and latency benchmarks | `bench/` | Results JSON + charts |

## Architecture

MarketForge is one C++ engine library used in two places: in a single matcher process for the live exchange, and inside the backtest worker for research, so live and simulated trading share the same matching code. The FastAPI gateway does not embed the engine: its worker processes forward commands to the matcher, which is the only writer of every symbol's sequencer, journal and book (M10).

&#91;embedded content: MarketForge architecture · 4 layers\]

Every command passes sequencer, risk gate and book in that order. The journal is the source of truth; Redis carries replies and market data; Postgres is a projection rebuilt from the journal.

**Order path**

1. A client submits an order to any gateway worker over REST.
2. The worker forwards it to the single matcher on a Redis stream (`mf:cmd`), with a deadline and its own reply stream.
3. The matcher assigns the order id and the symbol's `seq_no`, writes the intake log entry (request id, `client_order_id`) and then the journal record, and runs the command through the risk gate and book. Each batch is a group commit: intake log, then journal, both fsynced, before anything leaves the matcher.
4. The reply goes back on the worker's reply stream; market data and execution reports are published to Redis and fanned out to WebSocket clients by the gateway workers.
5. A separate projector replays the journals and writes Postgres in transactional batches, off the matching path.
6. Backtests drive the same engine in-process through the worker; no network on the backtest path.

On restart the matcher rebuilds each symbol from its newest snapshot plus the journal tail; the projector resumes from its stored cursor. docs/learning/m10.md has the details.

**Tech choices**

- Engine: C++20, CMake, GoogleTest, Google Benchmark, HdrHistogram.
- Bindings: pybind11, packaged with scikit-build-core.
- Python: 3.12, FastAPI, Pydantic, SQLAlchemy 2.0, Alembic, pandas, NumPy, pytest, Hypothesis.
- Infra: PostgreSQL, Redis, Docker Compose, GitHub Actions.

**Repo layout**

```text
marketforge/
├── engine/          # C++ library: book, matching, risk, journal
│   ├── include/mf/
│   ├── src/
│   ├── tests/       # GoogleTest + golden files
│   └── bench/       # Google Benchmark + latency harness
├── bindings/        # pybind11 module `mfengine`
├── reference/       # naive Python engine for differential tests
├── research/        # data adapters, backtester, strategies, costs, analytics
├── services/
│   ├── api/         # FastAPI gateway (no engine)
│   ├── matcher/     # the single matcher: engines, sequencers, journals
│   ├── projector/   # journals -> Postgres projection
│   └── worker/      # backtest job runner
├── dashboard/       # web UI
├── deploy/          # Dockerfiles, docker-compose.yml, Grafana
├── docs/            # design decisions, benchmark reports
├── CLAUDE.md
├── SPEC.md          # this document
└── Makefile
```

## C++ matching engine

The engine is a single-threaded, per-symbol limit order book in C++20 that turns a stream of commands into a stream of events, with no wall clock, no floats and no heap allocation on the hot path.

**Order model**

- Fields: `order_id` (uint64), `account_id`, `client_order_id`, `symbol`, `side`, `type`, `price` (int64 ticks), `qty` (int64), `time_in_force`, `seq_no`.
- Prices are integer ticks with a per-instrument tick size. Never use `double` for price or quantity.
- Time is a logical sequence number assigned by the sequencer. Exchange timestamps come from the injected clock (simulated during replay).

**Order types and time-in-force**

- Limit (GTC / DAY), Market, IOC (fill what you can, cancel the rest), FOK (fill completely or reject), Post-only (reject or re-price if it would cross).
- Stretch: stop / stop-limit, iceberg (display qty + hidden reserve).

**Commands**

- New, Cancel, Cancel-replace (modify).
- Modify rules: a quantity decrease keeps time priority; a price change or quantity increase loses priority (re-queued at the back).

**Matching rules**

1. Price-time priority: best price first, then FIFO within a price level.
2. An incoming order matches against resting orders until it is filled or the book no longer crosses.
3. Trade price is the resting order's price; the incoming order is the aggressor.
4. Partial fills leave the remainder resting (limit) or cancel it (IOC / market).
5. Self-trade prevention per account, configurable: cancel newest, cancel oldest, or cancel both.
6. A market order into an empty side is rejected with a reason code, not silently dropped.

**Data structures**

- Each side: price levels in a sorted structure (start with `std::map`, then benchmark a flat array indexed by tick offset around the mid).
- Each level: an intrusive doubly linked list of orders, plus cached total quantity and order count.
- `order_id → node` hash map for O(1) cancel and modify.
- Orders allocated from a pre-sized object pool. Best bid and ask cached and updated incrementally.

**Output events**

- Ack, Reject (with reason code), Fill and PartialFill (one per side), Cancelled, Replaced, Trade.
- Market data: L1 top of book, L2 aggregated depth (N levels, as deltas plus periodic snapshots), optional L3 order-by-order.
- Every event carries the `seq_no` of the command that caused it.

**Invariants (asserted in debug and test builds after every command)**

- The book is never crossed after matching.
- Level totals equal the sum of their orders.
- Fills conserve quantity: filled + remaining = original.
- Sequence numbers are strictly increasing.

**Session states:** pre-open, continuous, halted, closed. A halt rejects new orders but accepts cancels. Opening auction is a stretch goal.

**Scaling:** symbols are sharded, one matching thread per shard. No locks inside a book; the single-writer design is what makes replay deterministic.

## Pre-trade risk controls

Every order passes a risk gate before it reaches the book; a failed check returns a Reject with a specific reason code and never touches book state.

| Check | Rule | Reason code |
| --- | --- | --- |
| Max order size | qty ≤ per-instrument limit | `MAX_QTY` |
| Max notional | price × qty ≤ per-account limit | `MAX_NOTIONAL` |
| Price band / fat finger | limit price within X% of reference (last trade or mid) | `PRICE_BAND` |
| Position limit | resulting position within ± limit per symbol | `POSITION_LIMIT` |
| Buying power | open-order exposure + new order ≤ account credit | `INSUFFICIENT_CREDIT` |
| Open order count | open orders per account ≤ limit | `MAX_OPEN_ORDERS` |
| Order rate | token bucket per account (orders/sec) | `RATE_LIMIT` |
| Duplicate ID | `client_order_id` unique per account per session | `DUPLICATE_ID` |
| Kill switch | account-level and global; blocks new orders, optionally cancels all | `KILL_SWITCH` |
| Session state | no new orders while halted or closed | `MARKET_CLOSED` |

- Limits are loaded from a YAML config and can be changed at runtime through an admin endpoint.
- Risk state (positions, open exposure) updates from the engine's own fill events, so it stays in sync during replay.
- Benchmark the risk gate separately so you can quote its cost per order.

## Deterministic market replay and data

The journal of inbound commands is the source of truth: replaying it through the engine must reproduce the exact same events, and a test proves it by comparing output hashes.

**Journal**

- Append-only binary log of every accepted command, each record with `seq_no`, simulated timestamp, payload and a CRC32.
- Periodic book snapshots so replay can start mid-session.
- A `mf-replay` CLI that replays a journal and prints the SHA-256 of the output event stream.

**Determinism rules**

- No wall-clock reads inside the engine; time comes from an injected `Clock` (real or simulated).
- No iteration over unordered containers where order affects output.
- All randomness (synthetic flow, latency jitter) uses seeded generators; the seed is stored with the run.

**Replay modes:** as fast as possible (for backtests and benchmarks), real time, and scaled speed (e.g. 10×) for demos.

**Data sources (pick at least one)**

- Historical L2/L3 order book data, e.g. LOBSTER sample files or exchange public data dumps (crypto exchanges publish free trade and depth archives).
- Daily or minute bars for cross-sectional momentum on a stock or ETF universe.
- A synthetic order-flow generator: Poisson arrivals, noise traders, a simple market maker and momentum traders. It powers benchmarks, demos and tests without any external data.

**Ingestion:** adapters convert each source into the engine's command format and store normalized data as Parquet. Validate on load: monotonic timestamps, no negative sizes, no crossed snapshots.

## Backtesting framework and momentum strategies

Backtests run in Python against the real C++ engine through pybind11 bindings, so a strategy's orders are matched by the same code that runs the live exchange.

**Two backtest modes**

- **Event-driven (book replay):** historical or synthetic order flow is replayed into the engine and the strategy's orders interact with that liquidity. Includes queue-position estimation for resting orders and configurable order latency.
- **Vectorized (bars):** fast pandas/NumPy research loop on daily or minute bars for parameter sweeps. Uses the same cost models so results are comparable.

**Strategy interface**

- Callbacks: `on_start`, `on_market_data(event)`, `on_fill(fill)`, `on_timer(t)`, `on_end`.
- Strategies return order intents; a broker adapter turns them into engine commands.
- Strategies only see data with timestamp ≤ current simulated time (enforced by the data feed, not by convention).

**Momentum strategies to ship**

1. Time-series momentum: go long or short based on the sign of the trailing N-period return, volatility-scaled position sizing.
2. Cross-sectional momentum: rank a universe by 12-1 month return, long the top bucket and short the bottom, rebalance monthly.
3. Intraday order-book momentum: trade on short-horizon return plus order-book imbalance in replay mode.

**Research hygiene**

- Train/test split and walk-forward validation; parameter sweeps report out-of-sample results only.
- Guard against look-ahead bias (signal computed on t, executed at t+1 or later) and note survivorship bias in the universe.
- Every run stores config, data range, seed, engine version and git commit, so any result can be reproduced.

## Transaction costs, slippage and portfolio analytics

Every backtest reports gross and net results side by side, so the cost models visibly change the conclusion rather than sitting in a footnote.

**Cost and slippage models (each can be toggled)**

- Commissions and fees: per share, per contract or in bps; maker-taker fees and rebates.
- Spread: paid naturally in book-replay mode; half-spread charge in bar mode.
- Slippage: fixed bps, volatility-scaled, and the square-root market-impact model below.
- Latency slippage: price moves between signal time and the order's arrival at the engine.

```latex
\text{impact} = k \cdot \sigma_{\text{daily}} \cdot \sqrt{\frac{Q}{\text{ADV}}}
```

Here Q is order size, ADV is average daily volume and k is a calibration constant.

**Portfolio analytics**

- Returns: total, annualized, rolling; equity curve and drawdown curve.
- Risk-adjusted: Sharpe, Sortino, Calmar, max drawdown and its duration, volatility.
- Trading: hit rate, average win/loss, profit factor, turnover, average holding period, exposure.
- Execution: implementation shortfall per trade, cost breakdown (fees vs spread vs impact).
- Attribution: gross alpha vs total costs; beta and correlation to a benchmark.
- Capacity: how net Sharpe degrades as capital scales (re-run with larger size through the impact model).

**Output:** a tearsheet (HTML report plus JSON metrics) per run, stored in Postgres and viewable in the dashboard. Metric functions are tested against hand-computed examples.

## Services: FastAPI, PostgreSQL and Redis

A FastAPI gateway forwards commands to a single matcher process, which owns the engine (through pybind11), one sequencer and journal per symbol. A projector writes PostgreSQL from the journals, and market data fans out through Redis to WebSocket clients.

**REST endpoints**

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/orders` | Submit an order (runs risk checks, returns ack or reject) |
| PATCH | `/orders/{id}` | Cancel-replace (modify) |
| DELETE | `/orders/{id}` | Cancel |
| GET | `/orders/{id}` | Order status and fills |
| GET | `/book/{symbol}?depth=10` | L2 snapshot |
| GET | `/trades/{symbol}` | Recent trades |
| GET | `/accounts/{id}/positions` | Positions, PnL, open exposure |
| POST | `/backtests` | Queue a backtest from a config |
| GET | `/backtests/{id}` | Status, metrics, tearsheet link |
| POST | `/admin/risk-limits`, `/admin/kill-switch`, `/admin/session` | Risk and session control |
| GET | `/health`, `/metrics` | Liveness and Prometheus metrics |

**WebSocket channels**

- `/ws/marketdata/{symbol}`: L1, L2 deltas with sequence numbers (client detects gaps and re-snapshots), trades.
- `/ws/executions`: private execution reports per account.
- `/ws/backtests/{id}`: live progress and streaming equity curve.

**API conventions:** Pydantic models, generated OpenAPI docs, API-key auth per account, consistent error schema with reason codes, idempotency via `client_order_id`.

**PostgreSQL** (SQLAlchemy 2.0 async + Alembic migrations)

- `instruments`, `accounts`, `risk_limits`
- `orders`, `fills`, `positions`
- `backtest_runs` (config, seed, commit, status), `backtest_metrics`, `equity_points`
- Writes from the matching path are batched asynchronously so the database never sits on the critical path.

**Redis**

- Pub/sub (or Streams) for market-data fan-out to multiple API workers.
- Latest book snapshot and top-of-book cache.
- Rate-limit counters for the API layer.
- Job queue for backtests (e.g. arq or RQ) consumed by a separate worker container.

**Dashboard (recommended for demos):** a small web UI with a live price ladder, trade tape, order entry, and a backtest page with equity curve, drawdown and the gross-vs-net table.

## Testing strategy

The engine is trusted because a randomized differential test runs millions of commands through it and a deliberately simple reference engine and requires identical output.

**C++ (GoogleTest)**

- Unit tests per behavior: each order type, partial fills across several levels, cancel of a partially filled order, modify priority rules, self-trade prevention, market order into an empty book, unfillable FOK, IOC remainder cancel, every risk reject code.
- Golden-file tests: fixed scenarios with expected event streams checked into `tests/golden/`.
- Invariant checks after every command in test builds.
- Fuzzing (libFuzzer) on the command decoder and on random command sequences.
- ASan, UBSan and TSan builds in CI.

**Differential testing**

- A naive Python reference order book (lists, linear scans, obviously correct).
- Hypothesis or a seeded generator produces random command streams; both engines must emit identical events.
- Failing seeds are saved as regression cases.

**Determinism:** replay the same journal twice, and across a fresh process, and assert equal output hashes.

**Python (pytest)**

- Cost models and metrics against hand-computed examples.
- Backtester: a look-ahead test that fails if a strategy can see a future bar.
- API tests with httpx against real Postgres and Redis (testcontainers or the compose stack).

**Targets:** engine line coverage ≥ 90%; CI must be green before any milestone counts as done.

## Throughput and latency benchmarks

Benchmarks measure the engine core and the network gateway separately, and every number in the README is regenerated by `make bench` with the machine spec printed beside it.

**Engine core**

- Google Benchmark microbenchmarks: add, cancel, modify, aggressive match (1 level and multi-level), risk check.
- End-to-end replay: push several million synthetic commands through one book; report commands/sec and events/sec.
- Per-command latency histogram with HdrHistogram using `steady_clock` (or `rdtsc`): p50, p90, p99, p99.9, max, warm-up excluded.

**Gateway**

- REST order round-trip and WebSocket fan-out latency under load (k6 or Locust), reported separately from engine numbers.

**Optimization log (strong interview material)**

- Measure before and after each change: `new`/`delete` vs object pool, `std::map` vs flat tick array, intrusive vs `std::list`, branch layout, `-O3 -march=native`, LTO.
- Record each result in `bench/OPTIMIZATIONS.md` with the numbers.

**Reporting rules:** results saved as JSON in `bench/results/`, plotted into the README; report the CPU, OS, compiler and flags; never hand-type a number.

## Docker, CI and dev tooling

One command brings up the whole system, and CI blocks merges unless C++ and Python builds, tests, sanitizers and linters all pass.

- **docker compose services:** `postgres`, `redis`, `matcher` (engine, exactly one), `projector`, `api` (gateway, no engine), `worker` (backtests), `dashboard`; optional `prometheus` and `grafana`.
- **Multi-stage Dockerfile:** build the engine with CMake, package the Python extension with scikit-build-core, copy into a slim runtime image.
- **Makefile targets:** `build`, `test`, `bench`, `lint`, `fmt`, `up`, `down`, `replay`, `demo`.
- **GitHub Actions:** gcc and clang builds, sanitizer builds, pytest, clang-format, clang-tidy, ruff, mypy, Docker image build.
- **Config:** environment variables plus YAML for instruments and risk limits; structured JSON logs; Prometheus metrics (orders/sec, rejects by code, queue depth, latency).
- **pre-commit hooks** for formatting and linting.
- **CLAUDE.md** at the repo root with build commands, conventions (integer prices, no wall clock in engine, tests required per feature) and the current milestone.

## Build milestones for Claude Code

Build in twelve milestones, one per session, and do not start the next until the current one has passing tests in CI and a commit.

1. **Scaffold:** repo layout, CMake, pybind11 stub, pytest, GitHub Actions, CLAUDE.md. Done when an empty test passes in CI.
2. **Core book:** limit orders, price-time priority, partial fills, cancel, event output, invariants. Done when unit tests pass.
3. **Order types:** market, IOC, FOK, post-only, cancel-replace, self-trade prevention. Done when the Python reference engine and differential test agree on 1M random commands.
4. **Journal and replay:** binary journal, snapshots, `mf-replay`, injected clock. Done when the determinism hash test passes.
5. **Risk gate:** all checks in the risk table, YAML config, kill switch. Done when every reject code has a test.
6. **Benchmarks:** microbenchmarks, replay throughput, latency histogram, first optimization pass. Done when results JSON and `OPTIMIZATIONS.md` exist.
7. **Python bindings:** `mfengine` package built with scikit-build-core. Done when Python tests drive the engine.
8. **Data and backtester:** ingestion adapters, synthetic flow generator, event-driven and vectorized backtests, three momentum strategies. Done when an example run reproduces from its stored config.
9. **Costs and analytics:** fee, spread, slippage and impact models, metrics, tearsheet. Done when the gross-vs-net report renders and metric tests pass.
10. **Services:** FastAPI REST and WebSocket, Postgres schema and migrations, Redis fan-out, backtest worker. Done when API tests pass against the compose stack.
11. **Dashboard:** price ladder, trade tape, order entry, backtest results. Done when the demo script drives it end to end.
12. **Polish:** README, architecture diagram, final benchmarks, demo script, resume numbers updated from real results.

## Definition of done and README

The project is done when a stranger can clone it, run one command, see a live exchange and a backtest tearsheet, and reproduce every number in the README.

- [ ] `git clone` then `make up` gives a working system in under 5 minutes on a fresh machine.
- [ ] `make demo` starts the exchange, a synthetic market maker and a momentum trader, and opens the dashboard.
- [ ] All CI checks green; engine coverage ≥ 90%.
- [ ] README contains: one-paragraph summary, architecture diagram, key design decisions (integer prices, single writer per symbol, injected clock, journal as source of truth), benchmark table with machine spec, an example tearsheet with gross vs net, testing approach, limitations and future work.
- [ ] `bench/OPTIMIZATIONS.md` documents each optimization with before/after numbers.
- [ ] Resume bullets updated with measured figures (orders/sec, p99 latency, test count, cost drag on Sharpe).
