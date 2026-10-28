"""Integers that survive JavaScript (M11). A browser parses every JSON number into a
double, which holds integers exactly only up to 2**53 - 1: a larger one is rounded
silently. Every integer the API returns is therefore either

- bounded: order prices and quantities are capped at the gateway (`MAX_PRICE_TICKS`,
  `MAX_ORDER_QTY`), so ids, prices, quantities and per-order values stay far below
  2**53; counters (seq_no, md_seq, ids) grow by one per command, and 2**53 commands at
  the engine's measured peak rate would take about 20 years;
- or a decimal string: money aggregates (position cash, P&L, notional) are products
  and sums of prices and quantities, and journal timestamps are nanoseconds since 1970
  (about 1.8e18), so both are always strings; research outputs (metrics, configs) turn
  any integer beyond the range into a string (`stringify_big`).

`find_unsafe` is the backstop: the gateway refuses to send a response or a WebSocket
message that still holds an unsafe integer (a loud failure, never a rounded number).
"""

from __future__ import annotations

from typing import Any

MAX_SAFE = 2**53 - 1
MAX_PRICE_TICKS = 10**9  # per order, at the gateway: 10,000,000.00 at a 0.01 tick
MAX_ORDER_QTY = 10**9  # per order, at the gateway


class UnsafeInteger(ValueError):
    """An integer a JavaScript client would round."""


def _unsafe(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and abs(v) > MAX_SAFE


def find_unsafe(value: Any, path: str = "$") -> str | None:
    """The JSON path of the first integer beyond +-(2**53 - 1), or None."""
    if _unsafe(value):
        return path
    if isinstance(value, dict):
        for k, v in value.items():
            hit = find_unsafe(v, f"{path}.{k}")
            if hit:
                return hit
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            hit = find_unsafe(v, f"{path}[{i}]")
            if hit:
                return hit
    return None


def assert_safe(value: Any) -> Any:
    hit = find_unsafe(value)
    if hit:
        raise UnsafeInteger(f"integer beyond 2**53 - 1 at {hit}")
    return value


def stringify_big(value: Any) -> Any:
    """A copy in which every integer beyond +-(2**53 - 1) is a decimal string."""
    if _unsafe(value):
        return str(value)
    if isinstance(value, dict):
        return {k: stringify_big(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [stringify_big(v) for v in value]
    return value
