"""Exact conversion of decimal text to integer units (ticks, lots, micro-dollars).

Vendors publish prices as decimal strings. A float would round some of them (0.1 has no
binary representation), so text goes through ``Decimal`` and must land exactly on the
unit's grid: off-grid values are rejected, never rounded.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class OffGridError(ValueError):
    """A value that is not an exact multiple of its unit."""


def to_units(text: str | Decimal | int, unit: str | Decimal) -> int:
    """``text`` as an integer number of ``unit``s: ``to_units("0.9703", "0.0001") == 9703``."""
    if isinstance(text, float) or isinstance(text, bool):
        raise TypeError(f"refusing {type(text).__name__} {text!r}: pass decimal text")
    try:
        value = text if isinstance(text, Decimal) else Decimal(str(text).strip())
    except InvalidOperation:
        raise ValueError(f"not a decimal number: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"not a finite number: {text!r}")
    quotient = value / Decimal(unit)
    if quotient != quotient.to_integral_value():
        raise OffGridError(f"{text} is not a multiple of {unit}")
    return int(quotient)
