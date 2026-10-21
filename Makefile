# Prefer a uv-managed 3.12 (Homebrew's python@3.12 has a broken pyexpat on macOS 26);
# fall back to python3.12 on PATH. Override with `make PYTHON=/path/to/python3.12`.
PYTHON     ?= $(shell uv python find --managed-python 3.12 2>/dev/null || command -v python3.12)
VENV       := .venv
VENV_PY    := $(VENV)/bin/python
BUILD_DIR  := build
BUILD_TYPE ?= RelWithDebInfo
DRIVER     := $(BUILD_DIR)/engine/tools/mf_driver
# Seed for the long differential test; override with `make difftest-long SEED=7`.
SEED       ?= 20261020

.PHONY: venv build test test-cpp test-py difftest difftest-long difftest-replay clean

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

difftest: venv build
	$(VENV_PY) -m reference.difftest --driver $(DRIVER) --seed $(SEED) --n 100000

difftest-long: venv build
	$(VENV_PY) -m reference.difftest --driver $(DRIVER) --seed $(SEED) --n 1000000

difftest-replay: venv build
	$(VENV_PY) -m reference.difftest --driver $(DRIVER) --replay

clean:
	rm -rf $(BUILD_DIR)
