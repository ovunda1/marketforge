"""The type stubs (M7) describe the module editors see: every public name in mfengine
is in the stubs and every stubbed name exists, and mypy's stubtest agrees."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import mfengine as mf

PACKAGE = Path(mf.__file__).resolve().parent
STUB = PACKAGE / "_core.pyi"
ALLOWLIST = Path(__file__).with_name("stubtest_allowlist.txt")


def stub_names() -> set[str]:
    tree = ast.parse(STUB.read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return {n for n in names if not n.startswith("_")}


def test_package_ships_stubs_and_py_typed() -> None:
    assert STUB.is_file()
    assert (PACKAGE / "py.typed").is_file()


def test_every_public_name_is_stubbed_and_exists() -> None:
    public = {n for n in dir(mf._core) if not n.startswith("_")}
    assert public == stub_names()
    # The package re-exports all of them, plus the Command and Event unions.
    assert {n for n in dir(mf) if not n.startswith("_")} == public | {"Command", "Event"}


# Typing, not memory safety: skipped on the sanitized build, where the subprocess would
# import the instrumented extension without the ASan runtime (macOS ASan removes itself
# from DYLD_INSERT_LIBRARIES, so children do not inherit it). Every other run has it.
@pytest.mark.skipif(bool(os.environ.get("MF_EXPECT_MFENGINE")), reason="sanitized build run")
def test_stubtest_agrees_with_the_runtime_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy.stubtest", "mfengine", "--allowlist", str(ALLOWLIST)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
