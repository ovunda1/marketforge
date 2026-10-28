# MarketForge

Simulated exchange plus research backtester. A C++20 single-threaded, per-symbol
limit order book (`engine/`) is the core; Python (3.12) provides a backtesting and
analytics layer (`research/`) via pybind11 bindings (`bindings/`, module `mfengine`),
and FastAPI services (`services/`) expose it over REST/WebSocket. The same engine
library runs both the live exchange and backtests. `SPEC.md` is the full build spec.

## Current milestone: 11b (Dashboard redesign)

Build one milestone per session. Do not start the next milestone until the current
one has passing tests in CI and a commit.

At the end of every milestone, write `docs/learning/mN.md` (N = milestone number)
covering: what was built; the key files and functions to read first; each design
decision and why; the trade-offs and what was deliberately left out; and 5 questions
an interviewer might ask about this milestone, with short answers. It ships in the
milestone's commit.

## Build and test

```sh
make venv     # create .venv with python3.12 and install dev deps
make build    # configure + build the C++ engine with CMake (build/)
make test     # run GoogleTest (ctest) and pytest (includes the 100k-command difftest)
make difftest-long   # C++ engine vs Python reference on 1M commands (SEED=…)
make difftest-replay # replay saved failing cases in tests/difftest/regressions/
make sanitize        # ASan + UBSan build (build-asan/): ctest, then pytest on it (as CI)
make replay JOURNAL=… [SNAPSHOT=…]  # mf-replay: SHA-256 of a journal's event stream
make bench           # benchmarks (build-bench/: Release, walk off) -> bench/results/*.json + docs
make bench-smoke     # suite on the bench build, every benchmark at tiny sizes (as CI)
make bench-ab OPT=opt-N  # interleaved before/after of a logged optimization
make bench-docs      # re-render the *.md.in templates from bench/results/
make gate            # what every optimization must pass before it is measured
make venv-release    # .venv-release: mfengine in Release, invariant walk off (backtests)
make bench-bindings  # single-call vs batch cost of mfengine, dev and release installs
make clean    # remove build artifacts
```

Python always runs from `.venv` (python3.12). `make venv` prefers a uv-managed 3.12
(`uv python install 3.12`) because Homebrew's python@3.12 has a broken `pyexpat` on
macOS 26; override with `make PYTHON=/path/to/python3.12`.

Python bindings (M7, `bindings/`, package `mfengine`, docs/learning/m7.md): built by
scikit-build-core from `pyproject.toml`. `make venv` installs it editable with the
invariant walk on, rebuilt on import when sources change (build tree `build-py/`);
`make venv-release` is the Release, walk-off install for backtests, and
`mfengine.INVARIANT_CHECKS` says which one is loaded. The glue is thin: no matching or
risk logic in `bindings/`. Values cross by copy (events are checked plain values,
`mf/plain_value.hpp`). `SequenceError` (stale seq_no, backwards timestamp) is thrown
before any state changes; any other exception from `Engine::process` fails the binding
Engine, and later calls raise `EngineFailed`. Stubs: `bindings/mfengine/_core.pyi`,
checked by a test (names both ways, and mypy stubtest).

Data and backtester (M8, `research/`, docs/learning/m8.md): `docs/data.md` (sources,
terms, normalized Parquet schemas, validation on every load) and `docs/backtesting.md`
(event-driven replay through the engine, bar backtester, walk-forward, run records).
Prices stay integers end to end (`research.ticks`: Decimal text onto the tick grid, never
a float); bars are stored unadjusted with corporate actions as columns. Never commit
downloaded data (`data/` is git-ignored): manifests in `research/data/manifests/` pin
checksums; `TIINGO_API_KEY` comes from the environment only. Tests run offline (a
conftest fixture blocks sockets). Backtest assumptions that could flatter a strategy
default to the conservative choice (pessimistic queue, taken liquidity stays taken,
history wins ties) and are recorded in every run record. Bar strategies must pass
`check_causal`; sweeps report out-of-sample only. Runs use the release install
(`make backtest CONFIG=…`, `make reproduce RECORD=…`); `make examples` regenerates the
committed example runs, which the suite reproduces byte for byte.

Costs and analytics (M9, `research/backtest/costs.py`, `research/analytics/`,
docs/learning/m9.md): `[costs]` sub-tables turn components on (commission, maker_taker,
sec_fee with a dated, sourced schedule, spread, slippage, vol_slippage, impact, latency,
financing). Event mode charges fees only: the book's fill price already holds the
spread, which is measured as implementation shortfall, never charged again. Impact
inputs (vol, dollar ADV) use bars before the fill only. Gross includes risk-free
interest on cash (FRED DTB3, `make data-fred`, forward-filled, never back-filled); net
= gross - every cost component, exactly. Walk-forward selects on net excess Sharpe.
Every run writes `metrics.json` + `tearsheet.html` (rendered from the metrics alone,
no scripts, byte-reproducible, portfolio-level only: `check_portfolio_level`). Dividends
are in 1e-8 dollars. The showcase (`research/showcase/`, `make showcase`) has its
parameters fixed in git before the first real run; `TRIALS.md` logs every real-data run.

```sh
make backtest CONFIG=research/examples/tsmom_walkforward.toml
make reproduce RECORD=runs/<run>/record.json
make examples        # regenerate research/examples/runs/ (only after a deliberate change)
make data-binance    # one pinned Binance day; make data-tiingo [UPDATE_PINS=1] needs the key
make data-fred       # FRED DTB3, the risk-free rate (no key)
make showcase        # M9: tsmom and xsmom on Tiingo, gross vs net (not in CI; outputs committed)
```

Services (M10, `services/`, `deploy/`, docs/services.md, docs/learning/m10.md): exactly ONE
process, the matcher (`services/matcher/`), owns every Engine, sequencer and journal;
API workers (`services/api/`, never import mfengine: a test checks) forward requests over
the Redis stream `mf:cmd` and get replies on their own reply stream. Each batch is a group
commit: intake log (`<SYM>/intake.jsonl`: request id, client_order_id, order id) written
and fsynced BEFORE the first journal write, then journal + engine, journal fsync, and only
then replies and market data. Idempotency (client_order_id, Idempotency-Key) is decided in
the matcher. Restart: `JournalWriter::resume` (trims a torn tail), drop orphan intake
entries, newest snapshot + journal tail, new epoch. Postgres is a projection: the
projector replays the journals, one transaction per batch with a per-symbol cursor
(`python -m services.projector verify` compares with a fresh replay). Market data:
snapshot + `md_seq` deltas per epoch; slow WebSocket clients are resnapshotted or closed
(4008), never waited for. Secrets only from env / git-ignored `.env` (copy `.env.example`).

```sh
make up / make down          # the stack (needs .env); make gateway-smoke on a running stack
make test-services           # API suite on a fresh compose project mf-test (KEEP=1 keeps it)
make projection-rebuild      # Postgres projection rebuilt from the journals
```

Dashboard (M11, `dashboard/`, docs/dashboard.md, docs/learning/m11.md): React + TypeScript
(strict) + Vite, served by nginx in the compose stack (http://127.0.0.1:8080; `/api/*` and
`/ws/*` proxied to the gateway). Node from Homebrew; `make dashboard-deps` runs `npm ci`.
The market-data client (`src/lib/bookClient.ts`), the price parser (`src/lib/money.ts`) and
the backtest form rules (`src/lib/backtestForm.ts`) have Python twins; both sides run the
fixtures in `tests/fixtures/{marketdata,ticks,backtest_form}/` (regenerate market data with
`.venv/bin/python tests/services/marketdata_fixtures.py`). Prices stay integer ticks in the
browser (BigInt digits, never a float). Every integer the API returns is bounded below 2**53
or a string (`services/common/safe_json.py`); the gateway refuses an unsafe one and the
browser's `parseJson` throws on one. API keys: memory + sessionStorage only, never
localStorage. Live symbols are fictional (ACME, GLOBEX, INITECH; SMOKE for gateway-smoke);
real tickers only on backtest pages, labelled historical. Every reject and error code has a
message + fix in `dashboard/src/content/rejects.json` (a test compares with the enums). The
Overview's numbers come from results files at build time (`dashboard/facts.config.ts`):
`make facts` writes `results/tests.json`, `make difftest-long` writes `results/difftest.json`.
Practice accounts (demo mode, `MF_DEMO_MODE`): per-IP limit with `X-Forwarded-For` believed
only from `MF_TRUSTED_PROXIES`; at most 2 backtests queued/running, form only.
Design (M11b, docs/learning/m11b.md): Swiss style, white/#111, Hanken Grotesk, 12-column grid,
no radius/shadow/cards; green = buy, red = sell or error, gold only as a line or fill, never
text (`src/lib/contrast.test.ts`). Every user-visible sentence goes in `src/copy.ts` (a test
walks the sources); terms are `<Term k=…>` with definitions on `/glossary`.

```sh
make test-dashboard      # tsc + Vitest
make e2e                 # Playwright (headless Chromium) on a fresh test stack (KEEP=1 keeps it)
make facts               # every offline suite with JUnit output -> results/tests.json
```

`MF_CHECK_INVARIANTS` (CMake option, default ON) runs the O(N) order book consistency
walk after every command; benchmark builds turn it off. The `seq_no` monotonicity
check in `OrderBook::process` is always on.

Journal and replay (M4): `mf-journal` turns canonical text commands into a binary
journal, `mf-replay` replays one (optionally from a snapshot) and prints the SHA-256 of
the event stream. Formats: `docs/journal-format.md`, `docs/snapshot-format.md`. Order
ids are unique per session and checked in the book, not the sequencer, so replay
reproduces the reject.

Risk gate (M5, `docs/risk.md`): `Engine` = `RiskGate` in front of `OrderBook`; every
sequenced command goes through `Engine::process(command, timestamp)`, live and on
replay. Limits, kill switches and session transitions are journaled admin commands; the
YAML config (`mf_config`, yaml-cpp) only turns into such commands
(`mf_driver`/`mf-journal --config`). The rate limiter's only clock is the record
timestamp (`T <ns>` lines in text files). A fresh engine is CONTINUOUS with no limits
(M4 behavior). Risk goldens (`engine/tests/golden/risk/`) are C++ only: the Python
reference stays book-only.

Benchmarks (M6, `bench/`; results in `bench/RESULTS.md`, log in `bench/OPTIMIZATIONS.md`):
timing code lives only in `bench/` (the wall-clock scan covers all of `engine/`). Docs that
quote measurements are rendered from `*.md.in` templates out of `bench/results/*.json`:
never type a number into them (a test lints the templates and checks the rendered files are
current). `make bench` refuses to run on battery or in Low Power Mode (`FORCE=1` overrides
and is recorded). `bench/expected_digests.json` pins each workload's event stream: an
optimization must leave it unchanged. Each optimization: tests first, `make gate`, one
commit between tags `m6-opt-N-before`/`-after` (pushed), `make bench-ab OPT=opt-N`, an
entry in `bench/optimizations.json`.

Golden scenarios live in `engine/tests/golden/` (`*.cmds` + hand-derived `*.events`)
and run against both engines. Formats: `docs/event-format.md`; order-type rules:
`docs/order-types.md`.

Later milestones add `lint`, `fmt` and `demo` targets.

## Conventions

- **Integer prices and quantities.** Prices are `int64` ticks with a per-instrument
  tick size; quantities are `int64`. Never use `double`/`float` for price or qty,
  in C++ or Python.
- **No wall clock in the engine.** Time is a logical `seq_no` from the sequencer;
  timestamps come from an injected `Clock` (simulated during replay). No
  `std::chrono::system_clock`, `time()`, etc. inside `engine/`.
- **Determinism.** No iteration over unordered containers where order affects
  output. All randomness uses seeded generators and the seed is stored with the run.
- **Tests required for every feature.** Every behavior change ships with tests
  (GoogleTest for C++, pytest for Python). CI must be green before a milestone counts.
- Hot path: no heap allocation, no locks inside a book (single writer per symbol).
- **Commit messages:** short conventional-commit style: one summary line plus a few
  bullets at most.
- **PR descriptions:** brief summary, test results, and known limitations only.
