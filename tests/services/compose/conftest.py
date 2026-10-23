"""The compose-stack suite (M10, `make test-services`): runs against `make up` (with the
test override), over real HTTP, WebSocket, Redis and Postgres. Skipped unless
MF_COMPOSE_API names the stack, so `make test` stays offline."""

from __future__ import annotations

import pytest

from .stack import API, Account, compose, create_account


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "services/compose" in str(item.fspath):
            item.add_marker(pytest.mark.compose)
            if not API:
                item.add_marker(pytest.mark.skip(reason="MF_COMPOSE_API not set: run `make test-services`"))


@pytest.fixture(autouse=True)
def _no_network() -> None:
    """These tests talk to the stack: the offline guard of tests/conftest.py is lifted."""


@pytest.fixture(scope="session")
def admin() -> Account:
    return create_account("admin")


@pytest.fixture
def alice() -> Account:
    return create_account()


@pytest.fixture
def bob() -> Account:
    return create_account()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):  # type: ignore[no-untyped-def]
    """On a failure, attach the stack's recent logs to the report (CI keeps them)."""
    report = yield
    if report.when == "call" and report.failed and API:
        logs = compose("logs", "--no-color", "--tail", "60", check=False).stdout
        report.sections.append(("compose logs (last 60 lines per service)", logs))
    return report
