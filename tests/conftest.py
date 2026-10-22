"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "engine" / "tests" / "golden"
REGRESSIONS_DIR = REPO_ROOT / "tests" / "difftest" / "regressions"


def pytest_configure() -> None:
    """With MF_EXPECT_MFENGINE set (make sanitize), refuse to run unless mfengine was
    imported from that build tree, so a sanitizer run cannot silently test the editable
    install instead."""
    expected = os.environ.get("MF_EXPECT_MFENGINE")
    if expected:
        import mfengine

        loaded = Path(mfengine._core.__file__).resolve()
        if Path(expected).resolve() not in loaded.parents:
            raise RuntimeError(f"mfengine loaded from {loaded}, expected under {expected}")
        # The ASan runtime was preloaded for this process only. The sanitized tools the
        # tests start (mf_driver, mf-replay) link their own copy, and inheriting the
        # preload makes ASan abort them ("incompatible ASan runtimes"). macOS ASan
        # clears DYLD_INSERT_LIBRARIES itself; do the same for LD_PRELOAD.
        os.environ.pop("LD_PRELOAD", None)


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
