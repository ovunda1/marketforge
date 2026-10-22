# MarketForge

Simulated exchange plus research backtester. A C++20 single-threaded, per-symbol
limit order book (`engine/`) is the core; Python (3.12) provides a backtesting and
analytics layer (`research/`) via pybind11 bindings (`bindings/`, module `mfengine`),
and FastAPI services (`services/`) expose it over REST/WebSocket. The same engine
library runs both the live exchange and backtests. `SPEC.md` is the full build spec.

## Current milestone: 8 (Data and backtester)

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

Later milestones add `lint`, `fmt`, `up`, `down` and `demo` targets.

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
