"""Decimal text <-> integer ticks for user input (M11): the reference the dashboard's
`dashboard/src/lib/money.ts` must agree with, case for case
(`tests/fixtures/ticks/cases.json`, checked by both test suites).

Input is stricter than `research.ticks.to_units` (vendor data): a person types plain
digits with an optional fraction, nothing else ("1e3", ".5", "1.", "+1", "1,000" are
format errors). A value off the tick grid is an error naming the two nearest valid
prices, never rounded. No float is involved anywhere: the text is split into digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from services.common.safe_json import MAX_ORDER_QTY, MAX_PRICE_TICKS

_PRICE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_QTY = re.compile(r"^[0-9]+$")
_TICK = re.compile(r"^[0-9]+(\.[0-9]+)?$")


@dataclass
class InputError(ValueError):
    kind: str  # empty | format | off_grid | not_positive | too_large
    message: str
    suggestions: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


def _scaled(text: str) -> tuple[int, int]:
    """'12.50' -> (1250, 2): the digits as an integer and the number of decimals."""
    whole, _, frac = text.partition(".")
    return int(whole + frac), len(frac)


def _tick(tick_size: str) -> tuple[int, int]:
    if not _TICK.match(tick_size):
        raise ValueError(f"bad tick size {tick_size!r}")
    units, places = _scaled(tick_size)
    if units <= 0:
        raise ValueError(f"tick size must be positive, not {tick_size!r}")
    return units, places


def format_ticks(ticks: int | str, tick_size: str) -> str:
    """Ticks (an int or a decimal integer string) as decimal text with the tick's
    decimal places: format_ticks(4217, "0.01") == "42.17", (21, "0.05") == "1.05"."""
    n = int(ticks) if isinstance(ticks, str) else ticks
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("ticks must be an integer")
    units, places = _tick(tick_size)
    sign, digits = ("-" if n < 0 else ""), str(abs(n) * units)
    if places == 0:
        return sign + digits
    digits = digits.rjust(places + 1, "0")
    return f"{sign}{digits[:-places]}.{digits[-places:]}"


def parse_price(text: str, tick_size: str) -> int:
    """Typed price text -> ticks, or InputError."""
    t = text.strip()
    if not t:
        raise InputError("empty", "Enter a price.")
    if not _PRICE.match(t):
        raise InputError("format", "Use digits with an optional decimal point, like 42.15.")
    units, places = _tick(tick_size)
    value, decimals = _scaled(t)
    # Both on the same scale: value / 10^decimals and units / 10^places.
    scale = max(decimals, places)
    v, u = value * 10 ** (scale - decimals), units * 10 ** (scale - places)
    if v == 0:
        raise InputError("not_positive", "The price must be greater than zero.")
    if v % u:
        lower, upper = v // u, v // u + 1
        options = [format_ticks(x, tick_size) for x in (lower, upper) if 0 < x <= MAX_PRICE_TICKS]
        raise InputError("off_grid", f"Prices move in steps of {tick_size}. Try {' or '.join(options)}.", options)
    ticks = v // u
    if ticks > MAX_PRICE_TICKS:
        raise InputError("too_large", f"The highest price accepted is {format_ticks(MAX_PRICE_TICKS, tick_size)}.")
    return ticks


def parse_qty(text: str) -> int:
    """Typed quantity text (whole units) -> int, or InputError."""
    t = text.strip()
    if not t:
        raise InputError("empty", "Enter a quantity.")
    if not _QTY.match(t):
        raise InputError("format", "Use a whole number of shares, like 10.")
    q = int(t)
    if q == 0:
        raise InputError("not_positive", "The quantity must be at least 1.")
    if q > MAX_ORDER_QTY:
        raise InputError("too_large", f"The largest quantity accepted is {MAX_ORDER_QTY:,}.")
    return q
