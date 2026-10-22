# Prefer a uv-managed 3.12 (Homebrew's python@3.12 has a broken pyexpat on macOS 26);
# fall back to python3.12 on PATH. Override with `make PYTHON=/path/to/python3.12`.
PYTHON     ?= $(shell uv python find --managed-python 3.12 2>/dev/null || command -v python3.12)
VENV       := .venv
VENV_PY    := $(VENV)/bin/python
# Bindings (M7): a Release install with the invariant walk off, for backtests.
RELEASE_VENV := .venv-release
RELEASE_PY := $(RELEASE_VENV)/bin/python
# Test runner for the sanitized bindings: pytest only, no mfengine installed.
ASAN_VENV  := .venv-asan
ASAN_PY    := $(ASAN_VENV)/bin/python
BUILD_DIR  := build
BUILD_TYPE ?= RelWithDebInfo
ASAN_DIR   := build-asan
# Benchmarks (M6): Release, invariant walk off, harness on.
BENCH_DIR  := build-bench
BENCH_CMAKE := -DCMAKE_BUILD_TYPE=Release -DMF_CHECK_INVARIANTS=OFF -DMF_BUILD_BENCH=ON
DRIVER     := $(BUILD_DIR)/engine/tools/mf_driver
REPLAY     := $(BUILD_DIR)/engine/tools/mf-replay
# The ASan runtime to preload into Python for the sanitized bindings, from the compiler
# that built them. A shell command, not $(shell ...): Make expands a recipe before its
# first line runs, which on a fresh tree is before CMake has written the cache.
CXX_FOR_ASAN = $$(cmake -LA -N $(ASAN_DIR) | sed -n 's/^CMAKE_CXX_COMPILER:[A-Z]*=//p')
ASAN_PRELOAD_VAR = $(if $(filter Darwin,$(shell uname)),DYLD_INSERT_LIBRARIES,LD_PRELOAD)
# Seed for the long differential test; override with `make difftest-long SEED=7`.
SEED       ?= 20261020

.PHONY: venv venv-release build test test-cpp test-py sanitize difftest difftest-long difftest-replay \
        replay bench bench-build bench-test bench-smoke bench-ab bench-docs bench-bindings gate clean \
        backtest reproduce examples data-binance data-tiingo

venv: $(VENV)/.installed

# Editable, with the invariant walk on (mfengine.INVARIANT_CHECKS). Without build
# isolation, so importing mfengine rebuilds the extension when engine or binding
# sources changed (scikit-build-core's editable.rebuild, build tree in build-py/).
$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install 'scikit-build-core>=0.10'
	$(VENV_PY) -m pip install --no-build-isolation -Ceditable.rebuild=true -e '.[dev]'
	touch $@

# For backtests: a Release build of the bindings with the invariant walk off, in its
# own venv so the dev install stays checks-on. Reinstall after changing sources.
venv-release:
	test -x $(RELEASE_PY) || $(PYTHON) -m venv $(RELEASE_VENV)
	$(RELEASE_PY) -m pip install --upgrade pip 'scikit-build-core>=0.10'
	$(RELEASE_PY) -m pip install --no-build-isolation -Cbuild-dir=build-py-release \
	  -Ccmake.build-type=Release -Ccmake.define.MF_CHECK_INVARIANTS=OFF '.[dev]'
	$(RELEASE_PY) -c "import mfengine; assert not mfengine.INVARIANT_CHECKS; print('mfengine', mfengine.BUILD_TYPE, 'invariant checks off')"

build:
	cmake -S . -B $(BUILD_DIR) -DCMAKE_BUILD_TYPE=$(BUILD_TYPE)
	cmake --build $(BUILD_DIR) -j

test: test-cpp test-py

test-cpp: build
	ctest --test-dir $(BUILD_DIR) --output-on-failure --no-tests=error

# pytest includes the 100k-command differential test, which needs mf_driver.
test-py: venv build
	MF_DRIVER=$(DRIVER) $(VENV_PY) -m pytest

# ASan + UBSan build (as in CI): unit tests, then pytest against the instrumented tools.
# The bindings' tests then run in-process on the sanitized extension: Python itself is
# not instrumented, so the ASan runtime is preloaded and leak checks are off (the
# interpreter's own allocations at exit would be reported). They run from a venv
# without mfengine installed (.venv-asan): the editable install's import hook would
# otherwise win over PYTHONPATH, which MF_EXPECT_MFENGINE checks.
sanitize: venv
	cmake -S . -B $(ASAN_DIR) -DCMAKE_BUILD_TYPE=Debug -DMF_SANITIZE=ON -DMF_BUILD_BINDINGS=ON \
	  -DPython_EXECUTABLE=$(abspath $(VENV_PY))
	cmake --build $(ASAN_DIR) -j
	ctest --test-dir $(ASAN_DIR) --output-on-failure --no-tests=error
	MF_DRIVER=$(ASAN_DIR)/engine/tools/mf_driver $(VENV_PY) -m pytest
	test -x $(ASAN_PY) || $(PYTHON) -m venv $(ASAN_VENV)
	$(ASAN_PY) -m pip install -q 'pytest>=8' 'mypy>=1.11'
	preload=$$(bindings/asan_runtime.sh "$(CXX_FOR_ASAN)") && \
	$(ASAN_PRELOAD_VAR)=$$preload ASAN_OPTIONS=detect_leaks=0:abort_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
	  PYTHONMALLOC=malloc PYTHONPATH=$(ASAN_DIR)/bindings/python MF_EXPECT_MFENGINE=$(ASAN_DIR) \
	  MF_DRIVER=$(ASAN_DIR)/engine/tools/mf_driver $(ASAN_PY) -m pytest tests/bindings

difftest: venv build
	$(VENV_PY) -m reference.difftest --driver $(DRIVER) --seed $(SEED) --n 100000

# Both generator profiles: narrow (fixed mid) and wide (drifting mid, far-away orders).
difftest-long: venv build
	$(VENV_PY) -m reference.difftest --driver $(DRIVER) --seed $(SEED) --n 1000000
	$(VENV_PY) -m reference.difftest --driver $(DRIVER) --seed $(SEED) --n 1000000 --profile wide

difftest-replay: venv build
	$(VENV_PY) -m reference.difftest --driver $(DRIVER) --replay

# Replay a journal and print the SHA-256 of its event stream:
#   make replay JOURNAL=run.mfj [SNAPSHOT=snap-000000001000.mfsnap]
replay: build
	@test -n "$(JOURNAL)" || { echo "usage: make replay JOURNAL=<file> [SNAPSHOT=<file>]"; exit 1; }
	$(REPLAY) $(JOURNAL) $(if $(SNAPSHOT),--snapshot $(SNAPSHOT))

# --- Benchmarks (M6, bench/) ---------------------------------------------------------

bench-build:
	cmake -S . -B $(BENCH_DIR) $(BENCH_CMAKE)
	cmake --build $(BENCH_DIR) -j

# The whole test suite on the benchmark configuration (Release, walk off), including the
# harness tests and the pinned workload digests.
bench-test: bench-build
	ctest --test-dir $(BENCH_DIR) --output-on-failure --no-tests=error

# Every number: replay throughput and latency on both workloads, microbenchmarks, then
# the docs re-rendered from the JSON. Refuses to run on battery unless FORCE=1.
bench: venv bench-build
	$(VENV_PY) -m bench.run --build-dir $(BENCH_DIR) $(if $(FORCE),--force)

# CI: tiny sizes, schema checks, pinned digests; never fails on speed.
bench-smoke: venv bench-test
	$(VENV_PY) -m bench.run --build-dir $(BENCH_DIR) --smoke

# Before/after of a logged optimization (bench/optimizations.json): make bench-ab OPT=opt-1
bench-ab: venv bench-build
	$(VENV_PY) -m bench.ab $(or $(OPT),all) $(if $(FORCE),--force)

bench-docs: venv
	$(VENV_PY) -m bench.render

# Per-command cost of the bindings, one process() call each vs one process_batch(), on
# the dev (checks on) and release (checks off) installs. Refuses battery unless FORCE=1.
bench-bindings: venv venv-release
	$(VENV_PY) -m bench.bindings --out bench/results/bindings-dev.json $(if $(FORCE),--force)
	$(RELEASE_PY) -m bench.bindings --out bench/results/bindings-release.json $(if $(FORCE),--force)
	$(VENV_PY) -m bench.render

# --- Research (M8, research/) -----------------------------------------------------------

# Runs use the release install (Release, invariant walk off); `make venv-release` after
# changing engine or binding sources. Records and results land in runs/ (git-ignored).
#   make backtest CONFIG=research/examples/tsmom_walkforward.toml
backtest:
	@test -n "$(CONFIG)" || { echo "usage: make backtest CONFIG=<config.toml>"; exit 1; }
	test -x $(RELEASE_PY) || $(MAKE) venv-release
	$(RELEASE_PY) -m research run $(CONFIG) --out runs

#   make reproduce RECORD=runs/<run>/record.json
reproduce:
	@test -n "$(RECORD)" || { echo "usage: make reproduce RECORD=<record.json>"; exit 1; }
	test -x $(RELEASE_PY) || $(MAKE) venv-release
	$(RELEASE_PY) -m research reproduce $(RECORD)

# Regenerate the committed example runs (research/examples/runs/), which the test suite
# reproduces byte for byte. Only after a deliberate change to results.
EXAMPLES := obmom_synthetic tsmom_walkforward xsmom_walkforward
examples:
	test -x $(RELEASE_PY) || $(MAKE) venv-release
	rm -rf research/examples/runs
	for c in $(EXAMPLES); do \
	  $(RELEASE_PY) -m research run research/examples/$$c.toml --out research/examples/runs --name $$c || exit 1; \
	done
	rm -f research/examples/runs/*/journal.mfj

# Real data (docs/data.md): downloaded into data/ (git-ignored), checked against pinned
# checksums, normalized to Parquet. Tiingo needs TIINGO_API_KEY in the environment; the
# first download needs UPDATE_PINS=1, then commit the manifest.
data-binance: venv
	$(VENV_PY) -m research.data.download research/data/manifests/binance-um-kncusdt-2024-03-12.json

data-tiingo: venv
	$(VENV_PY) -m research.data.download research/data/manifests/tiingo-etf-daily.json $(if $(UPDATE_PINS),--update-pins)

# What every optimization must pass before it is measured: the full suite with the
# invariant walk on, the sanitizers, the long difftest on both profiles, the suite on
# the benchmark build, and the benchmark workloads replayed with the walk on.
gate: test sanitize difftest-long bench-test
	$(VENV_PY) -m bench.crosscheck --bench-dir $(BENCH_DIR) --replay $(REPLAY)

clean:
	rm -rf $(BUILD_DIR) $(ASAN_DIR) $(BENCH_DIR) .bench-ab build-py build-py-release
