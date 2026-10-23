"""The demo market's liquidity bot (M11): something to watch on the dashboard.

    python -m services.demo     (compose service `demo-bot`, demo mode only)

A plain API client, like any other: it trades through the gateway's REST endpoints with
two ordinary accounts (a maker and a taker), never touching the matcher directly. Per
symbol, a mean-reverting random walk moves a mid price; the maker keeps a ladder of
limit orders around it and the taker now and then crosses the spread with an IOC
order, so trades print. Everything is integer ticks.

`Quoter` is the deterministic part (seeded, no I/O, unit-tested): given its state it
says which orders to cancel and which to place. The loop around it sends them and
resyncs the maker's resting orders from `GET /orders` every few steps (users trade
against them too). The accounts' keys are kept in MF_BOT_STATE (a volume), so a
restarted bot cancels its own leftovers instead of piling up new ones.

Configuration: MF_API_URL, MF_BOT_SYMBOLS ("ACME:4200,GLOBEX:1850,INITECH:640": symbol
and reference mid in ticks), MF_BOT_SEED, MF_BOT_INTERVAL_S, MF_BOT_STATE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("mf.demo")


@dataclass(frozen=True)
class Action:
    op: str  # "new" | "cancel"
    side: str = ""
    price: int = 0
    qty: int = 0
    tif: str = "gtc"
    order_id: int = 0
    role: str = "maker"  # which account sends it


@dataclass
class Quoter:
    """One symbol's quotes. `levels` limit orders a side, `step` ticks apart, the best
    `half_spread` ticks from the mid; the mid pulls back toward `ref` (kappa) and moves
    by about `sigma` ticks a step."""

    symbol: str
    ref: int
    rng: random.Random
    levels: int = 5
    half_spread: int = 1
    step: int = 1
    sigma: float = 1.2
    kappa: float = 0.02
    take_prob: float = 0.35
    mid: int = 0
    resting: dict[int, tuple[str, int, int]] = field(default_factory=dict)  # order id -> (side, price, qty)

    def __post_init__(self) -> None:
        if self.mid == 0:
            self.mid = self.ref
        self.floor = max(self.ref // 2, self.levels * self.step + self.half_spread + 1)
        self.ceiling = self.ref * 2

    def move(self) -> None:
        drift = self.kappa * (self.ref - self.mid)
        self.mid = int(round(self.mid + drift + self.rng.gauss(0.0, self.sigma)))
        self.mid = min(max(self.mid, self.floor), self.ceiling)

    def ladder(self) -> dict[tuple[str, int], int]:
        """(side, price) -> quantity wanted."""
        want: dict[tuple[str, int], int] = {}
        for k in range(self.levels):
            want[("buy", self.mid - self.half_spread - k * self.step)] = 10 * self.rng.randint(1, 12)
            want[("sell", self.mid + self.half_spread + k * self.step)] = 10 * self.rng.randint(1, 12)
        return want

    def plan(self) -> list[Action]:
        """One step: move the mid, cancel quotes that left the ladder, add missing ones,
        maybe take liquidity."""
        self.move()
        want = self.ladder()
        actions = []
        have = {}
        for oid, (side, price, qty) in sorted(self.resting.items()):
            if (side, price) in want and (side, price) not in have:
                have[(side, price)] = oid
            else:
                actions.append(Action("cancel", order_id=oid))
        for (side, price), qty in sorted(want.items()):
            if (side, price) not in have:
                actions.append(Action("new", side, price, qty))
        if self.rng.random() < self.take_prob:
            side = self.rng.choice(("buy", "sell"))
            through = self.half_spread + self.step * self.rng.randint(0, 2)
            price = self.mid + through if side == "buy" else self.mid - through
            actions.append(Action("new", side, price, 10 * self.rng.randint(1, 6), tif="ioc", role="taker"))
        return actions


# --- I/O ----------------------------------------------------------------------------------

def parse_symbols(text: str) -> list[tuple[str, int]]:
    out = []
    for item in text.split(","):
        sym, _, ref = item.strip().partition(":")
        out.append((sym, int(ref)))
    return out


async def accounts(state_file: Path) -> dict[str, str]:
    """The maker's and taker's keys, created once (directly in the database, like the
    accounts CLI) and kept in the state file."""
    if state_file.is_file():
        return json.loads(state_file.read_text())  # type: ignore[no-any-return]
    from services.api.accounts import create
    from services.common.config import Settings

    settings = Settings.from_env()
    keys = {role: (await create(settings, f"demo-{role}", "trader"))["api_key"] for role in ("maker", "taker")}
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(keys))
    state_file.chmod(0o600)
    return keys  # type: ignore[return-value]


async def run(url: str, symbols: list[tuple[str, int]], seed: int, interval_s: float, state_file: Path) -> None:
    import httpx

    keys = await accounts(state_file)
    rng = random.Random(seed)
    quoters = [Quoter(sym, ref, random.Random(rng.getrandbits(64))) for sym, ref in symbols]
    async with httpx.AsyncClient(base_url=url, timeout=10) as client:
        async def send(q: Quoter, a: Action) -> None:
            headers = {"X-API-Key": keys[a.role]}
            if a.op == "cancel":
                await client.delete(f"/orders/{a.order_id}", headers=headers)
                q.resting.pop(a.order_id, None)
                return
            r = await client.post("/orders", headers=headers, json={
                "symbol": q.symbol, "side": a.side, "type": "limit", "tif": a.tif, "price": a.price, "qty": a.qty,
                "client_order_id": uuid.uuid4().hex})
            if r.status_code == 201 and a.role == "maker" and r.json()["status"] in ("open", "partially_filled"):
                q.resting[r.json()["order_id"]] = (a.side, a.price, r.json()["leaves_qty"])

        async def resync(q: Quoter) -> None:
            r = await client.get("/orders", params={"status": "open", "symbol": q.symbol, "limit": 500},
                                 headers={"X-API-Key": keys["maker"]})
            if r.status_code == 200:
                q.resting = {o["order_id"]: (o["side"], o["price"], o["leaves_qty"]) for o in r.json()["orders"]}

        for q in quoters:  # a restarted bot starts clean: cancel what an earlier run left
            await resync(q)
            for oid in list(q.resting):
                await send(q, Action("cancel", order_id=oid))
        step = 0
        while True:
            for q in quoters:
                try:
                    if step and step % 20 == 0:
                        await resync(q)
                    for a in q.plan():
                        await send(q, a)
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    log.warning("%s: %s (retrying next step)", q.symbol, e)
            step += 1
            await asyncio.sleep(interval_s)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per order would drown the log
    asyncio.run(run(os.environ.get("MF_API_URL", "http://api:8000"),
                    parse_symbols(os.environ.get("MF_BOT_SYMBOLS", "ACME:4200,GLOBEX:1850,INITECH:640")),
                    int(os.environ.get("MF_BOT_SEED", "1")), float(os.environ.get("MF_BOT_INTERVAL_S", "0.5")),
                    Path(os.environ.get("MF_BOT_STATE", "/var/lib/marketforge/bot/keys.json"))))
    return 0
