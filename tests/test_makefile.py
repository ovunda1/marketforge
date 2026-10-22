"""Makefile recipes that are easy to get subtly wrong (M8)."""

import shutil
import subprocess

import pytest

from conftest import REPO_ROOT


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_sanitize_reads_the_compiler_after_configuring(tmp_path) -> None:
    # Make expands a whole recipe before running its first line, so a compiler read from
    # the CMake cache by $(shell ...) is read before `cmake -S -B` has configured a fresh
    # tree, and the ASan runtime silently fell back to plain `c++`. The read must happen
    # in the shell, when the preload line runs.
    asan_dir = tmp_path / "never-configured"
    out = subprocess.run(
        ["make", "-n", "sanitize", f"ASAN_DIR={asan_dir}"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    preload = [line for line in out.splitlines() if "asan_runtime.sh" in line]
    assert len(preload) == 1, out
    assert f"cmake -LA -N {asan_dir}" in preload[0]
    assert "asan_runtime.sh c++" not in preload[0]


def test_asan_runtime_refuses_an_empty_compiler() -> None:
    # A failed cache read must not fall back to the default compiler's runtime.
    result = subprocess.run(
        [str(REPO_ROOT / "bindings" / "asan_runtime.sh"), ""], capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "empty compiler" in result.stderr
