"""Golden scenarios against both engines: hand-derived expected output, not each other."""

from pathlib import Path

import pytest

from conftest import GOLDEN_DIR
from reference.difftest import run_driver
from reference.run import run_lines

SCENARIOS = sorted(p.stem for p in GOLDEN_DIR.glob("*.cmds"))


def expected(name: str) -> list[str]:
    return (GOLDEN_DIR / f"{name}.events").read_text().splitlines()


def test_scenarios_are_discovered() -> None:
    assert len(SCENARIOS) >= 12


@pytest.mark.parametrize("name", SCENARIOS)
def test_reference_matches_golden(name: str) -> None:
    lines = (GOLDEN_DIR / f"{name}.cmds").read_text().splitlines()
    assert list(run_lines(lines)) == expected(name)


@pytest.mark.parametrize("name", SCENARIOS)
def test_driver_matches_golden(name: str, driver: Path) -> None:
    assert run_driver(driver, GOLDEN_DIR / f"{name}.cmds") == expected(name)
