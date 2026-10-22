# Prefer a uv-managed 3.12 (Homebrew's python@3.12 has a broken pyexpat on macOS 26);
# fall back to python3.12 on PATH. Override with `make PYTHON=/path/to/python3.12`.
PYTHON     ?= $(shell uv python find --managed-python 3.12 2>/dev/null || command -v python3.12)
VENV       := .venv
VENV_PY    := $(VENV)/bin/python
BUILD_DIR  := build
BUILD_TYPE ?= RelWithDebInfo
ASAN_DIR   := build-asan
# Benchmarks (M6): Release, invariant walk off, harness on.
BENCH_DIR  := build-bench
BENCH_CMAKE := -DCMAKE_BUILD_TYPE=Release -DMF_CHECK_INVARIANTS=OFF -DMF_BUILD_BENCH=ON
DRIVER     := $(BUILD_DIR)/engine/tools/mf_driver
REPLAY     := $(BUILD_DIR)/engine/tools/mf-replay
# Seed for the long differential test; override with `make difftest-long SEED=7`.
SEED       ?= 20261020

.PHONY: venv build test test-cpp test-py sanitize difftest difftest-long difftest-replay replay \
        bench bench-build bench-test bench-smoke bench-ab bench-docs gate clean

venv: $(VENV)/.installed

$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -e '.[dev]'
	touch $@

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
sanitize: venv
	cmake -S . -B $(ASAN_DIR) -DCMAKE_BUILD_TYPE=Debug -DMF_SANITIZE=ON
	cmake --build $(ASAN_DIR) -j
	ctest --test-dir $(ASAN_DIR) --output-on-failure --no-tests=error
	MF_DRIVER=$(ASAN_DIR)/engine/tools/mf_driver $(VENV_PY) -m pytest

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

# What every optimization must pass before it is measured: the full suite with the
# invariant walk on, the sanitizers, the long difftest on both profiles, the suite on
# the benchmark build, and the benchmark workloads replayed with the walk on.
gate: test sanitize difftest-long bench-test
	$(VENV_PY) -m bench.crosscheck --bench-dir $(BENCH_DIR) --replay $(REPLAY)

clean:
	rm -rf $(BUILD_DIR) $(ASAN_DIR) $(BENCH_DIR) .bench-ab
