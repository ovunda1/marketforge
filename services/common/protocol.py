"""Messages between the gateway's workers and the single matcher (M10).

A `Request` goes onto the `mf:cmd` Redis stream; the matcher answers with a `Reply` on
the requesting worker's own reply stream. Both are JSON. Prices and quantities are
integers (ticks and units) end to end.

Reply status:
- ``ok``: sequenced, journaled and accepted by the engine.
- ``rejected``: sequenced and journaled, and the engine rejected it (`reason` holds the
  engine's reject code). Replay reproduces the reject.
- ``error``: never sequenced (`code`: EXPIRED, NOT_FOUND, UNKNOWN_SYMBOL,
  IDEMPOTENCY_CONFLICT, SYMBOL_HALTED, BAD_REQUEST), except ENGINE_FAILED, where the
  command was journaled and the engine failed processing it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

ORDER_OPS = ("new", "cancel", "replace")
ADMIN_OPS = ("session", "instrument_limits", "account_limits", "kill_switch")
OPS = ORDER_OPS + ADMIN_OPS

SIDES = ("buy", "sell")
ORDER_TYPES = ("limit", "market")
TIFS = ("gtc", "ioc", "fok", "day")
STPS = ("cancel_newest", "cancel_oldest", "cancel_both")
SESSION_STATES = ("pre_open", "continuous", "halted", "closed")
INSTRUMENT_LIMITS = ("max_qty", "band_bps", "reference_price")
ACCOUNT_LIMITS = ("max_notional", "position_limit", "credit", "max_open_orders", "rate_per_sec", "rate_burst")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Request:
    request_id: str
    reply_to: str
    deadline_ns: int  # the matcher drops the request (EXPIRED) once its clock passes this
    account_id: int  # who sent it; admin commands carry their target in params
    op: str
    symbol: str | None = None  # new orders and admin commands (None: every symbol)
    order_id: int | None = None  # cancel and replace
    key: str | None = None  # client_order_id (new) or Idempotency-Key (cancel, replace)
    params: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return _canonical(asdict(self))

    @classmethod
    def from_json(cls, text: str | bytes) -> Request:
        data = json.loads(text)
        return cls(**data)

    def payload_hash(self) -> str:
        """What a retry must repeat exactly for its key to count as the same request."""
        return hashlib.sha256(_canonical([self.op, self.symbol, self.order_id, self.params]).encode()).hexdigest()


@dataclass(frozen=True)
class Reply:
    request_id: str
    status: str  # ok | rejected | error
    symbol: str | None = None
    order_id: int | None = None
    seq_no: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    replayed: bool = False  # an idempotent retry: the original's order_id and seq_no, no events
    reason: str | None = None  # the engine's reject code when rejected
    code: str | None = None  # the error code when status is error
    message: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)  # admin: one per symbol

    def to_json(self) -> str:
        return _canonical(asdict(self))

    @classmethod
    def from_json(cls, text: str | bytes) -> Reply:
        return cls(**json.loads(text))
