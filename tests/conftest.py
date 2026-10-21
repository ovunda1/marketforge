"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "engine" / "tests" / "golden"
REGRESSIONS_DIR = REPO_ROOT / "tests" / "difftest" / "regressions"


@pytest.fixture(scope="session")
def driver() -> Path:
    """The C++ mf_driver executable. Fails (never skips) when it has not been built."""
    path = Path(os.environ.get("MF_DRIVER", REPO_ROOT / "build" / "engine" / "tools" / "mf_driver"))
    if not path.is_file():
        pytest.fail(f"mf_driver not found at {path}: run `make build` or set MF_DRIVER")
    return path


@pytest.fixture(scope="session")
def tools(driver: Path) -> dict[str, Path]:
    """mf-journal and mf-replay, built next to mf_driver."""
    found = {name: driver.parent / name for name in ("mf-journal", "mf-replay")}
    for name, path in found.items():
        if not path.is_file():
            pytest.fail(f"{name} not found at {path}: run `make build`")
    return found
