"""The shared client fixtures (M11): the Python side of the cross-checks with the
dashboard. The same JSON files are run by dashboard/src/lib/*.test.ts."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from marketdata_fixtures import OUT, render
from research.ticks import OffGridError, to_units
from services.client import BookClient
from services.common.money import InputError, format_ticks, parse_price, parse_qty

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MARKETDATA = json.loads(OUT.read_text())["cases"]
TICKS = json.loads((FIXTURES / "ticks" / "cases.json").read_text())


@pytest.mark.parametrize("case", MARKETDATA, ids=[c["name"] for c in MARKETDATA])
def test_the_python_client_matches_every_market_data_fixture(case: dict) -> None:
    client = BookClient(case["symbol"])
    for i, step in enumerate(case["steps"]):
        result = client.on_message(step["message"])
        got = {"result": result, "synced": client.synced, "epoch": client.epoch, "md_seq": client.md_seq,
               "resnapshots": client.resnapshots, "bids": [list(x) for x in client.bids()],
               "asks": [list(x) for x in client.asks()]}
        assert got == step["expect"], f"step {i}"


def test_the_market_data_fixtures_regenerate_byte_for_byte() -> None:
    """From a real matcher: if the protocol changes, the fixtures (and both clients' tests) must follow."""
    assert render() == OUT.read_text()


def test_the_fixtures_cover_every_resync_path() -> None:
    results = [s["expect"]["result"] for c in MARKETDATA for s in c["steps"]]
    kinds = {s["message"]["type"] for c in MARKETDATA for s in c["steps"]}
    assert False in results and {"snapshot", "update", "resnapshot", "pong"} <= kinds
    assert len({s["message"]["epoch"] for c in MARKETDATA for s in c["steps"] if "epoch" in s["message"]}) >= 2


@pytest.mark.parametrize("case", TICKS["parse_price"], ids=[f"{c['text']!r}@{c['tick_size']}" for c in TICKS["parse_price"]])
def test_price_text_to_ticks(case: dict) -> None:
    if "ticks" in case:
        assert parse_price(case["text"], case["tick_size"]) == case["ticks"]
    else:
        with pytest.raises(InputError) as err:
            parse_price(case["text"], case["tick_size"])
        assert err.value.kind == case["error"]
        if "suggestions" in case:
            assert err.value.suggestions == case["suggestions"]


@pytest.mark.parametrize("case", [c for c in TICKS["parse_price"] if "ticks" in c or c["error"] == "off_grid"],
                         ids=lambda c: f"{c['text']!r}@{c['tick_size']}")
def test_research_ticks_agrees_on_every_well_formed_price(case: dict) -> None:
    """The vendor-data converter (M8) and the input parser land on the same ticks."""
    if "ticks" in case:
        assert to_units(case["text"].strip(), case["tick_size"]) == case["ticks"]
    else:
        with pytest.raises(OffGridError):
            to_units(case["text"], case["tick_size"])


@pytest.mark.parametrize("case", TICKS["parse_qty"], ids=lambda c: repr(c["text"]))
def test_quantity_text(case: dict) -> None:
    if "qty" in case:
        assert parse_qty(case["text"]) == case["qty"]
    else:
        with pytest.raises(InputError) as err:
            parse_qty(case["text"])
        assert err.value.kind == case["error"]


@pytest.mark.parametrize("case", TICKS["format"], ids=lambda c: f"{c['ticks']}@{c['tick_size']}")
def test_ticks_to_text(case: dict) -> None:
    assert format_ticks(case["ticks"], case["tick_size"]) == case["text"]
    if isinstance(case["ticks"], int):  # and back again, exactly
        assert Decimal(case["text"]) == case["ticks"] * Decimal(case["tick_size"])


def test_no_float_in_the_conversion() -> None:
    source = (Path(__file__).resolve().parents[2] / "services" / "common" / "money.py").read_text()
    assert "float(" not in source and "Decimal(" not in source  # digits only, not even Decimal
