"""Placeholder for Milestone 1: proves pytest runs under the project venv."""

import sys


def test_python_version() -> None:
    assert sys.version_info >= (3, 12)
