# Prefer a uv-managed 3.12 (Homebrew's python@3.12 has a broken pyexpat on macOS 26);
# fall back to python3.12 on PATH. Override with `make PYTHON=/path/to/python3.12`.
PYTHON     ?= $(shell uv python find --managed-python 3.12 2>/dev/null || command -v python3.12)
VENV       := .venv
VENV_PY    := $(VENV)/bin/python
BUILD_DIR  := build
BUILD_TYPE ?= RelWithDebInfo

.PHONY: venv build test test-cpp test-py clean

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

test-py: venv
	$(VENV_PY) -m pytest

clean:
	rm -rf $(BUILD_DIR)
