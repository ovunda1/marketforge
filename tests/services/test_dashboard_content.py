"""Dashboard content the Python side can check (M11): every engine reject reason and
every gateway error code has a plain-language message with a suggested fix, and the
live market lists only fictional symbols."""

from __future__ import annotations

import json
from pathlib import Path

import mfengine as mf
import pytest

from services.api.app import ERROR_CODES
from services.common.instruments import load_instruments

REPO = Path(__file__).resolve().parents[2]
REJECTS = json.loads((REPO / "dashboard/src/content/rejects.json").read_text())

# Real listings a simulated price must never be mistaken for: the backtest universe and
# well-known stocks and coins.
REAL = set(json.loads((REPO / "research/data/manifests/tiingo-etf-daily.json").read_text())["universe"]) | {
    "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "NFLX", "IBM", "INTC", "AMD", "ORCL", "JPM",
    "BAC", "WMT", "KO", "PEP", "DIS", "BTC", "BTCUSD", "ETH", "ETHUSD", "BRK.B", "V", "MA", "XOM", "CVX"}


@pytest.mark.parametrize("section,codes", [
    ("engine", list(mf.RejectReason.__members__)),
    ("admin", list(mf.AdminRejectReason.__members__)),
    ("cancel", list(mf.CancelReason.__members__)),
    ("api", list(ERROR_CODES)),
])
def test_every_code_has_a_message_and_nothing_else_does(section: str, codes: list[str]) -> None:
    assert set(REJECTS[section]) == set(codes)
    for code, entry in REJECTS[section].items():
        assert entry["title"] and entry["message"].endswith(".") and len(entry["message"]) > 20, code
        if section in ("engine", "api"):
            assert entry["fix"].endswith("."), f"{code}: a suggested fix"


def test_messages_are_plain_language_not_codes() -> None:
    for section in ("engine", "admin", "api"):
        for code, entry in REJECTS[section].items():
            text = " ".join(entry.values())
            assert code not in text and "_" not in text.replace("docs/risk.md", ""), code


@pytest.mark.parametrize("config", ["instruments.yaml", "instruments.test.yaml"])
def test_the_live_market_lists_only_fictional_symbols(config: str) -> None:
    listed = {i.symbol for i in load_instruments(REPO / "deploy/config" / config)}
    assert not listed & REAL, listed & REAL
    if config == "instruments.yaml":
        assert {"ACME", "GLOBEX", "INITECH", "SMOKE"} == listed
