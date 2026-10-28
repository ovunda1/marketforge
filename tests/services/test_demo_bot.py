"""The demo market's liquidity bot (M11): its quoting is seeded and deterministic, and
every order it would send is a sane integer-tick limit order."""

from __future__ import annotations

import random

from services.demo.bot import Action, Quoter, parse_symbols
from services.common.safe_json import MAX_ORDER_QTY, MAX_PRICE_TICKS


def simulate(seed: int, steps: int, ref: int = 4200) -> list[list[Action]]:
    """Plans `steps` steps, acting as if every new maker order rests and every cancel works."""
    q = Quoter("ACME", ref, random.Random(seed))
    next_id, out = 1, []
    for _ in range(steps):
        actions = q.plan()
        for a in actions:
            if a.op == "cancel":
                q.resting.pop(a.order_id)
            elif a.role == "maker":
                q.resting[next_id] = (a.side, a.price, a.qty)
                next_id += 1
        out.append(actions)
    return out


def test_the_same_seed_gives_the_same_orders() -> None:
    assert simulate(7, 300) == simulate(7, 300)
    assert simulate(7, 300) != simulate(8, 300)


def test_quotes_are_positive_bounded_integer_ticks_and_never_crossed() -> None:
    for seed in range(5):
        q = Quoter("ACME", 640, random.Random(seed))
        for _ in range(2000):
            plan = q.plan()
            makers = [a for a in plan if a.op == "new" and a.role == "maker"]
            for a in plan:
                if a.op == "new":
                    assert isinstance(a.price, int) and isinstance(a.qty, int)
                    assert 0 < a.price <= MAX_PRICE_TICKS and 0 < a.qty <= MAX_ORDER_QTY
                    assert a.tif == ("ioc" if a.role == "taker" else "gtc")
            bids = [a.price for a in makers if a.side == "buy"]
            asks = [a.price for a in makers if a.side == "sell"]
            if bids and asks:
                assert max(bids) < min(asks)
            assert q.floor <= q.mid <= q.ceiling


def test_the_ladder_is_kept_not_churned() -> None:
    """Quotes that are still wanted stay (keeping their queue position); only the ones the
    mid moved away from are cancelled."""
    plans = simulate(3, 200)
    placed = sum(1 for p in plans for a in p if a.op == "new" and a.role == "maker")
    assert placed < 200 * 10 / 2  # far fewer than a full ladder every step


def test_takers_cross_the_spread_so_trades_print() -> None:
    plans = simulate(1, 500)
    takers = [a for p in plans for a in p if a.role == "taker"]
    assert len(takers) > 100 and {a.side for a in takers} == {"buy", "sell"}


def test_symbol_configuration() -> None:
    assert parse_symbols("ACME:4200, INITECH:640") == [("ACME", 4200), ("INITECH", 640)]
