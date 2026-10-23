"""Shared helpers for the service tests (M10): requests as the gateway sends them, a
deterministic clock, a matcher in a temp dir, and the crash and power-loss simulation."""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import mfengine as mf

from services.common.events import event_to_dict
from services.common.instruments import Instrument
from services.common.protocol import Reply, Request
from services.matcher.core import BatchResult, Matcher

T0 = 1_760_000_000_000_000_000  # ns
FAR = 10**19 - 1  # a deadline that never passes
INSTRUMENTS = [Instrument(symbol="AAA", tick_size="0.01"), Instrument(symbol="BBB", tick_size="0.05")]


class FakeClock:
    """Wall-clock stand-in: returns `now` and never moves unless told to."""

    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ns: int) -> None:
        self.now += ns


_ids = itertools.count(1)


def rid() -> str:
    return f"req-{next(_ids)}"


def new(account: int, symbol: str, side: str, price: int, qty: int, coid: str | None = None, *,
        type: str = "limit", tif: str = "gtc", post_only: bool = False, stp: str = "cancel_newest",
        deadline: int = FAR, request_id: str | None = None) -> Request:
    return Request(request_id=request_id or rid(), reply_to="r:test", deadline_ns=deadline,
                   account_id=account, op="new", symbol=symbol,
                   key=coid if coid is not None else f"c-{next(_ids)}",
                   params={"side": side, "type": type, "tif": tif, "price": price, "qty": qty,
                           "post_only": post_only, "stp": stp})


def cancel(account: int, order_id: int, key: str | None = None, *, deadline: int = FAR) -> Request:
    return Request(request_id=rid(), reply_to="r:test", deadline_ns=deadline, account_id=account,
                   op="cancel", order_id=order_id, key=key)


def replace(account: int, order_id: int, price: int, qty: int, key: str | None = None) -> Request:
    return Request(request_id=rid(), reply_to="r:test", deadline_ns=FAR, account_id=account,
                   op="replace", order_id=order_id, key=key, params={"price": price, "qty": qty})


def admin(op: str, symbol: str | None = None, **params: Any) -> Request:
    return Request(request_id=rid(), reply_to="r:test", deadline_ns=FAR, account_id=1, op=op,
                   symbol=symbol, params=params)


def open_matcher(data_dir: Path, clock: FakeClock, instruments: Sequence[Instrument] = INSTRUMENTS,
                 **kw: Any) -> Matcher:
    return Matcher.open(data_dir, instruments, clock=clock, **kw)


def run(matcher: Matcher, requests: Sequence[Request], batch: int = 7) -> list[Reply]:
    """Feeds requests in fixed-size batches; returns the replies in request order."""
    replies: list[Reply] = []
    for i in range(0, len(requests), batch):
        replies.extend(matcher.process_batch(requests[i:i + batch]).replies)
    return replies


def random_flow(seed: int, n: int, symbols: Sequence[str] = ("AAA",), accounts: Sequence[int] = (1, 2, 3),
                mid: int = 10_000) -> list[Request]:
    """New orders around a fixed mid, plus cancels and replaces of the requester's own
    earlier orders. Order ids are predicted: the matcher numbers new orders 1, 2, 3... in
    request order (every request here is valid, so each new order takes the next id)."""
    rng = random.Random(seed)
    out: list[Request] = []
    owned: dict[int, list[int]] = {a: [] for a in accounts}
    next_id = 1
    for _ in range(n):
        account = rng.choice(accounts)
        r = rng.random()
        if r < 0.65 or not owned[account]:
            side = rng.choice(["buy", "sell"])
            off = rng.randint(-5, 8)
            price = mid - off if side == "buy" else mid + off
            tif = rng.choices(["gtc", "ioc", "fok", "day"], [6, 2, 1, 1])[0]
            typ = "market" if rng.random() < 0.05 else "limit"
            out.append(new(account, rng.choice(symbols), side, price, rng.randint(1, 20), type=typ, tif=tif,
                           post_only=rng.random() < 0.05,
                           stp=rng.choice(["cancel_newest", "cancel_oldest", "cancel_both"])))
            owned[account].append(next_id)
            next_id += 1
        elif r < 0.85:
            out.append(cancel(account, rng.choice(owned[account])))
        else:
            out.append(replace(account, rng.choice(owned[account]), mid + rng.randint(-6, 6), rng.randint(1, 25)))
    return out


def journal_events(journal: Path) -> list[tuple[int, list[dict[str, Any]]]]:
    """(seq_no, event dicts) per record, by replaying the journal through a fresh engine."""
    engine = mf.Engine()
    return [(rec.seq_no, [event_to_dict(e) for e in engine.process(rec.command, rec.timestamp)])
            for rec in mf.JournalReader(journal)]


def journal_path(data_dir: Path, symbol: str) -> Path:
    return data_dir / symbol / "journal.mfj"


def intake_path(data_dir: Path, symbol: str) -> Path:
    return data_dir / symbol / "intake.jsonl"


def power_loss(durable: dict[Path, int], *, keep_unsynced_journal: bool) -> None:
    """What an OS crash can leave on disk. Unsynced intake data is always lost (worst
    case for the intake log). Unsynced journal data is either lost too, or kept, as if
    the kernel had written those pages back on its own before the crash: the worst case
    for "a journal record whose intake entry is gone"."""
    for path, size in durable.items():
        if path.name == "journal.mfj" and keep_unsynced_journal:
            continue
        with path.open("r+b") as f:
            f.truncate(size)


def journaled_keys(data_dir: Path, symbols: Iterable[str]) -> list[tuple[int, str]]:
    """(account, client_order_id) of every journaled new order, from the intake logs."""
    from services.matcher.intake import IntakeLog

    out = []
    for s in symbols:
        last = mf.JournalWriter.resume(journal_path(data_dir, s))
        last_seq = last.last_seq_no
        last.close()
        out += [(e.account_id, e.key) for e in IntakeLog.load(intake_path(data_dir, s))
                if e.op == "new" and e.seq_no <= last_seq]
    return out


def book(matcher: Matcher, symbol: str) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    engine = matcher.engine(symbol)
    return engine.depth(mf.Side.BUY), engine.depth(mf.Side.SELL)


def all_events(results: Iterable[BatchResult]) -> list[dict[str, Any]]:
    return [e for r in results for reply in r.replies for e in reply.events]
