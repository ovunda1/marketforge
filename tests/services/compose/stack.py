"""Helpers for the compose-stack tests (M10): the stack's URL, `docker compose` commands,
fresh accounts, and polling."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import httpx


REPO_ROOT = Path(__file__).resolve().parents[3]
API = os.environ.get("MF_COMPOSE_API", "")
WS = API.replace("http://", "ws://").replace("https://", "wss://")
COMPOSE = shlex.split(os.environ.get(
    "MF_COMPOSE", "docker compose -p mf-test -f deploy/docker-compose.yml -f deploy/docker-compose.test.yml"))

T = TypeVar("T")


def compose(*args: str, check: bool = True, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([*COMPOSE, *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return result


@dataclass(frozen=True)
class Account:
    account_id: int
    key: str

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.key}


def create_account(role: str = "trader") -> Account:
    out = compose("exec", "-T", "api", "python", "-m", "services.api.accounts", "create",
                  "--name", f"test-{uuid.uuid4().hex[:8]}", "--role", role).stdout
    data = json.loads(out.strip().splitlines()[-1])
    return Account(data["account_id"], data["api_key"])


def client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=30)


def coid() -> str:
    return uuid.uuid4().hex


def order(symbol: str, side: str, price: int, qty: int, **kw: Any) -> dict[str, Any]:
    return {"symbol": symbol, "side": side, "type": "limit", "tif": "gtc", "price": price, "qty": qty,
            "client_order_id": coid()} | kw


def eventually(fn: Callable[[], T | None], timeout: float = 30, every: float = 0.2, what: str = "") -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what or fn}")
        time.sleep(every)


def verify_projection() -> dict[str, Any]:
    """Replays every journal into a fresh in-memory projection inside the projector
    container and compares it with Postgres; returns the tool's JSON report."""
    out = compose("exec", "-T", "projector", "python", "-m", "services.projector", "verify", check=False)
    report = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {}
    assert out.returncode == 0 and report.get("ok"), f"{out.stdout}\n{out.stderr}"
    return report


def wait_projected() -> dict[str, Any]:
    """The projection has caught up with every journal (and matches a fresh replay)."""
    def caught_up() -> dict[str, Any] | None:
        out = compose("exec", "-T", "projector", "python", "-m", "services.projector", "verify", check=False)
        if out.returncode != 0:
            return None
        report = json.loads(out.stdout.strip().splitlines()[-1])
        return report if report.get("ok") else None
    return eventually(caught_up, timeout=60, every=1.0, what="projection to catch up")
