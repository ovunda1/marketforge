# MarketForge

Simulated exchange plus research backtester. A C++20 single-threaded, per-symbol
limit order book (`engine/`) is the core; Python (3.12) provides a backtesting and
analytics layer (`research/`) via pybind11 bindings (`bindings/`, module `mfengine`),
and FastAPI services (`services/`) expose it over REST/WebSocket. The same engine
library runs both the live exchange and backtests. `SPEC.md` is the full build spec.

## Current milestone: 3 (Order types)

Build one milestone per session. Do not start the next milestone until the current
one has passing tests in CI and a commit.

## Build and test

```sh
make venv     # create .venv with python3.12 and install dev deps
make build    # configure + build the C++ engine with CMake (build/)
make test     # run GoogleTest (ctest) and pytest (includes the 100k-command difftest)
make difftest-long   # C++ engine vs Python reference on 1M commands (SEED=…)
make difftest-replay # replay saved failing cases in tests/difftest/regressions/
make clean    # remove build artifacts
```

Python always runs from `.venv` (python3.12). `make venv` prefers a uv-managed 3.12
(`uv python install 3.12`) because Homebrew's python@3.12 has a broken `pyexpat` on
macOS 26; override with `make PYTHON=/path/to/python3.12`.

The pybind11 stub (`bindings/`) is off by default: build it with
`cmake -S . -B build -DMF_BUILD_BINDINGS=ON`.

`MF_CHECK_INVARIANTS` (CMake option, default ON) runs the O(N) order book consistency
walk after every command; benchmark builds turn it off. The `seq_no` monotonicity
check in `OrderBook::process` is always on.

Golden scenarios live in `engine/tests/golden/` (`*.cmds` + hand-derived `*.events`)
and run against both engines. Formats: `docs/event-format.md`; order-type rules:
`docs/order-types.md`.

Later milestones add `bench`, `lint`, `fmt`, `up`, `down`, `replay` and `demo` targets.

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
