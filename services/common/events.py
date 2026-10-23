"""Engine values as JSON-ready dicts and back (M10): events for replies, executions and
the projection; commands for the projection. Enum values are lower case (``buy``,
``gtc``); reject and admin reject reasons keep their upper-case codes."""

from __future__ import annotations

from typing import Any

import mfengine as mf

from services.common.protocol import Request


def _low(e: Any) -> str:
    return e.name.lower()


def event_to_dict(e: Any) -> dict[str, Any]:
    if isinstance(e, mf.Ack):
        return {"type": "ack", "seq_no": e.seq_no, "order_id": e.order_id, "side": _low(e.side),
                "order_type": _low(e.type), "tif": _low(e.tif), "post_only": e.post_only, "stp": _low(e.stp),
                "price": e.price, "qty": e.qty}
    if isinstance(e, mf.Reject):
        return {"type": "reject", "seq_no": e.seq_no, "order_id": e.order_id, "reason": e.reason.name}
    if isinstance(e, mf.Fill):
        return {"type": "fill", "seq_no": e.seq_no, "trade_id": e.trade_id, "order_id": e.order_id,
                "account_id": e.account_id, "side": _low(e.side), "liquidity": _low(e.liquidity),
                "price": e.price, "qty": e.qty, "leaves_qty": e.leaves_qty, "cum_qty": e.cum_qty}
    if isinstance(e, mf.Cancelled):
        return {"type": "cancelled", "seq_no": e.seq_no, "order_id": e.order_id, "cancelled_qty": e.cancelled_qty,
                "reason": _low(e.reason)}
    if isinstance(e, mf.Trade):
        return {"type": "trade", "seq_no": e.seq_no, "trade_id": e.trade_id, "price": e.price, "qty": e.qty,
                "maker_order_id": e.maker_order_id, "taker_order_id": e.taker_order_id,
                "aggressor_side": _low(e.aggressor_side)}
    if isinstance(e, mf.Replaced):
        return {"type": "replaced", "seq_no": e.seq_no, "order_id": e.order_id, "price": e.price, "qty": e.qty,
                "leaves_qty": e.leaves_qty, "priority_kept": e.priority_kept}
    if isinstance(e, mf.SessionChanged):
        return {"type": "session_changed", "seq_no": e.seq_no, "state": _low(e.state)}
    if isinstance(e, mf.AdminAck):
        return {"type": "admin_ack", "seq_no": e.seq_no, "kind": _low(e.kind)}
    if isinstance(e, mf.AdminReject):
        return {"type": "admin_reject", "seq_no": e.seq_no, "reason": e.reason.name}
    raise TypeError(f"not an engine event: {e!r}")


def _enum(cls: Any, value: str) -> Any:
    return cls[value.upper()]


def command_from_request(req: Request, *, order_id: int) -> mf.Command:
    """The engine command for one symbol (seq_no 0: the matcher assigns it). Raises
    ValueError or KeyError on a malformed request; the gateway validates first."""
    p = req.params
    if req.op == "new":
        return mf.NewOrder(seq_no=0, order_id=order_id, account_id=req.account_id, side=_enum(mf.Side, p["side"]),
                           type=_enum(mf.OrderType, p.get("type", "limit")), tif=_enum(mf.TimeInForce, p.get("tif", "gtc")),
                           post_only=bool(p.get("post_only", False)), stp=_enum(mf.StpMode, p.get("stp", "cancel_newest")),
                           price=int(p.get("price", 0)), qty=int(p["qty"]))
    if req.op == "cancel":
        return mf.Cancel(seq_no=0, order_id=order_id)
    if req.op == "replace":
        return mf.Replace(seq_no=0, order_id=order_id, price=int(p["price"]), qty=int(p["qty"]))
    if req.op == "session":
        return mf.SetSession(seq_no=0, state=_enum(mf.SessionState, p["state"]))
    if req.op == "instrument_limits":
        return mf.SetInstrumentLimits(seq_no=0, limits=mf.InstrumentLimits(
            max_qty=p.get("max_qty"), band_bps=p.get("band_bps"), reference_price=p.get("reference_price")))
    if req.op == "account_limits":
        rate = None
        if p.get("rate_per_sec") is not None or p.get("rate_burst") is not None:
            rate = mf.RateLimit(per_sec=int(p["rate_per_sec"]), burst=int(p["rate_burst"]))
        return mf.SetAccountLimits(seq_no=0, account_id=p.get("target_account"), limits=mf.AccountLimits(
            max_notional=p.get("max_notional"), position_limit=p.get("position_limit"), credit=p.get("credit"),
            max_open_orders=p.get("max_open_orders"), rate=rate))
    if req.op == "kill_switch":
        return mf.KillSwitch(seq_no=0, engaged=bool(p["engaged"]), account_id=p.get("target_account"),
                             cancel_open=bool(p.get("cancel_open", False)))
    raise ValueError(f"unknown op {req.op!r}")


def command_to_dict(c: Any) -> dict[str, Any]:
    """A journaled command as a dict, for the projection."""
    if isinstance(c, mf.NewOrder):
        return {"op": "new", "order_id": c.order_id, "account_id": c.account_id, "side": _low(c.side),
                "order_type": _low(c.type), "tif": _low(c.tif), "post_only": c.post_only, "stp": _low(c.stp),
                "price": c.price, "qty": c.qty}
    if isinstance(c, mf.Cancel):
        return {"op": "cancel", "order_id": c.order_id}
    if isinstance(c, mf.Replace):
        return {"op": "replace", "order_id": c.order_id, "price": c.price, "qty": c.qty}
    if isinstance(c, mf.SetSession):
        return {"op": "session", "state": _low(c.state)}
    if isinstance(c, mf.SetInstrumentLimits):
        lim = c.limits
        return {"op": "instrument_limits", "limits": {"max_qty": lim.max_qty, "band_bps": lim.band_bps,
                                                       "reference_price": lim.reference_price}}
    if isinstance(c, mf.SetAccountLimits):
        acc = c.limits
        return {"op": "account_limits", "account_id": c.account_id, "limits": {
            "max_notional": acc.max_notional, "position_limit": acc.position_limit, "credit": acc.credit,
            "max_open_orders": acc.max_open_orders,
            "rate_per_sec": acc.rate.per_sec if acc.rate else None,
            "rate_burst": acc.rate.burst if acc.rate else None}}
    if isinstance(c, mf.KillSwitch):
        return {"op": "kill_switch", "account_id": c.account_id, "engaged": c.engaged, "cancel_open": c.cancel_open}
    raise TypeError(f"not an engine command: {c!r}")
