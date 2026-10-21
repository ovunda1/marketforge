"""Canonical command lines (docs/event-format.md): parse and format."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NewOrder:
    seq: int
    order_id: int
    account: int
    side: str  # "B" | "S"
    type: str  # "LMT" | "MKT"
    tif: str  # "GTC" | "IOC" | "FOK"
    post_only: bool
    stp: str  # "CN" | "CO" | "CB"
    price: int
    qty: int


@dataclass(frozen=True)
class Cancel:
    seq: int
    order_id: int


@dataclass(frozen=True)
class Replace:
    seq: int
    order_id: int
    price: int
    qty: int  # new total quantity, including what has already filled


Command = NewOrder | Cancel | Replace

# Strict integer syntax, identical to the C++ parser (std::from_chars): no '+', no '_'.
_UNSIGNED = re.compile(r"[0-9]+")
_SIGNED = re.compile(r"-?[0-9]+")


def _unsigned(token: str, field: str) -> int:
    if not _UNSIGNED.fullmatch(token):
        raise ValueError(f"bad {field} {token!r}")
    return int(token)


def _signed(token: str, field: str) -> int:
    if not _SIGNED.fullmatch(token):
        raise ValueError(f"bad {field} {token!r}")
    return int(token)


def _choice(token: str, field: str, allowed: tuple[str, ...]) -> str:
    if token not in allowed:
        raise ValueError(f"bad {field} {token!r}")
    return token


def _expect_fields(tokens: list[str], n: int) -> None:
    if len(tokens) != n:
        raise ValueError(f"{tokens[0]!r} expects {n - 1} fields, got {len(tokens) - 1}")


def parse_command(line: str) -> Command | None:
    """Parses one line; None for blank or comment-only lines. Raises ValueError."""
    tokens = line.split("#", 1)[0].split()
    if not tokens:
        return None
    tag = tokens[0]
    if tag == "N":
        _expect_fields(tokens, 11)
        return NewOrder(
            seq=_unsigned(tokens[1], "seq"),
            order_id=_unsigned(tokens[2], "id"),
            account=_unsigned(tokens[3], "acct"),
            side=_choice(tokens[4], "side", ("B", "S")),
            type=_choice(tokens[5], "type", ("LMT", "MKT")),
            tif=_choice(tokens[6], "tif", ("GTC", "IOC", "FOK")),
            post_only=_choice(tokens[7], "post_only", ("0", "1")) == "1",
            stp=_choice(tokens[8], "stp", ("CN", "CO", "CB")),
            price=_signed(tokens[9], "px"),
            qty=_signed(tokens[10], "qty"),
        )
    if tag == "C":
        _expect_fields(tokens, 3)
        return Cancel(seq=_unsigned(tokens[1], "seq"), order_id=_unsigned(tokens[2], "id"))
    if tag == "R":
        _expect_fields(tokens, 5)
        return Replace(
            seq=_unsigned(tokens[1], "seq"),
            order_id=_unsigned(tokens[2], "id"),
            price=_signed(tokens[3], "px"),
            qty=_signed(tokens[4], "qty"),
        )
    raise ValueError(f"unknown command {tag!r}")


def format_command(command: Command) -> str:
    if isinstance(command, NewOrder):
        c = command
        return (
            f"N {c.seq} {c.order_id} {c.account} {c.side} {c.type} {c.tif} "
            f"{int(c.post_only)} {c.stp} {c.price} {c.qty}"
        )
    if isinstance(command, Cancel):
        return f"C {command.seq} {command.order_id}"
    return f"R {command.seq} {command.order_id} {command.price} {command.qty}"
