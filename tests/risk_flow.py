"""Seeded random command streams with risk limits, admin commands and clock lines (M5).

Written in the canonical text format (docs/event-format.md). The limits are tight and
time often stands still, so every risk reject code, kill-switch cancels, session
transitions (valid and invalid) and DAY expiries all occur. Used to write the committed
fixture engine/tests/journal/v2.cmds and by tests/test_risk_replay.py.

    python tests/risk_flow.py SEED N > out.cmds
"""

from __future__ import annotations

import random
import sys

# Config commands at the start of every stream (seq_nos 1..3).
HEADER = [
    "L 1 40 300 100",                 # max_qty 40, band 3%, reference 100
    "A 2 * 3000 60 6000 3 20 5",      # defaults: notional, position, credit, open, rate
    "A 3 1 1500 30 3000 2 5 2",       # account 1 is tighter
]

ACCOUNTS = (1, 2, 3, 4)
STATES = ("PRE_OPEN", "CONTINUOUS", "HALTED", "CLOSED")
# The next state on the way back to CONTINUOUS.
TOWARD_CONTINUOUS = {"CLOSED": "PRE_OPEN", "PRE_OPEN": "CONTINUOUS", "HALTED": "CONTINUOUS"}


def generate(seed: int, n: int) -> list[str]:
    """`n` commands (seq_no 1..n), plus clock lines, starting with HEADER."""
    rng = random.Random(seed)
    lines = [f"# risk flow seed={seed} n={n}", *HEADER]
    session = "CONTINUOUS"
    killed: set[str] = set()  # "*" for the global switch
    now = 0
    next_id = 1
    for seq in range(len(HEADER) + 1, n + 1):
        if rng.random() < 0.3:  # time often stands still: bursts hit the rate limit
            now += rng.randrange(200_000_000)
            lines.append(f"T {now}")
        # Abnormal states do not last long, so most orders reach the limit checks.
        if session != "CONTINUOUS" and rng.random() < 0.1:
            session = TOWARD_CONTINUOUS[session]
            lines.append(f"S {seq} {session}")
            continue
        if killed and rng.random() < 0.1:
            who = rng.choice(sorted(killed))
            killed.discard(who)
            lines.append(f"K {seq} {who} OFF KEEP")
            continue
        r = rng.randrange(1000)
        acct = rng.choice(ACCOUNTS)
        if r < 8:
            if session != "CONTINUOUS" and rng.random() < 0.8:
                target = TOWARD_CONTINUOUS[session]
            else:
                target = rng.choice(STATES)  # may be invalid: AREJ
            allowed = (session, target) in {
                ("CLOSED", "PRE_OPEN"), ("PRE_OPEN", "CONTINUOUS"), ("CONTINUOUS", "HALTED"),
                ("HALTED", "CONTINUOUS"), ("PRE_OPEN", "CLOSED"), ("CONTINUOUS", "CLOSED"),
                ("HALTED", "CLOSED"),
            }
            if allowed:
                session = target
            lines.append(f"S {seq} {target}")
        elif r < 14:
            who = "*" if rng.random() < 0.2 else str(acct)
            on = rng.random() < 0.5
            # OFF CANCEL is invalid: AREJ
            action = "CANCEL" if rng.random() < (0.5 if on else 0.2) else "KEEP"
            if on:
                killed.add(who)
            elif action == "KEEP":
                killed.discard(who)
            lines.append(f"K {seq} {who} {'ON' if on else 'OFF'} {action}")
        elif r < 17:
            max_qty = rng.choice(["0", "30", "40", "60", "-"])  # 0 is invalid: AREJ
            band = rng.choice(["200", "300", "500", "-"])
            lines.append(f"L {seq} {max_qty} {band} 100")
        elif r < 20:
            who = "*" if rng.random() < 0.3 else str(acct)
            rate = rng.choice(["5 2", "20 5", "- -"])
            lines.append(f"A {seq} {who} 3000 60 {rng.choice(['3000', '6000', '-1'])} 3 {rate}")  # -1: AREJ
        elif r < 170:
            lines.append(f"C {seq} {recent_id(rng, next_id)}")
        elif r < 260:
            lines.append(f"R {seq} {recent_id(rng, next_id)} {price(rng)} {rng.randint(1, 45)}")
        else:
            order_id = next_id if rng.random() < 0.95 else rng.randrange(1, next_id + 1)
            next_id += 1
            side = rng.choice("BS")
            stp = rng.choice(("CN", "CO", "CB"))
            qty = rng.randint(1, 50) if rng.random() < 0.5 else rng.randint(1, 8)
            if r < 320:
                lines.append(f"N {seq} {order_id} {acct} {side} MKT IOC 0 {stp} 0 {qty}")
            else:
                tif = rng.choice(("GTC", "GTC", "DAY", "DAY", "IOC", "FOK"))
                post_only = 1 if tif in ("GTC", "DAY") and rng.random() < 0.1 else 0
                px = 0 if rng.random() < 0.01 else price(rng)  # 0: INVALID_PRICE
                lines.append(f"N {seq} {order_id} {acct} {side} LMT {tif} {post_only} {stp} {px} {qty}")
    return lines


def recent_id(rng: random.Random, next_id: int) -> int:
    """Mostly one of the last 30 ids, so cancels and replaces often find a live order."""
    return rng.randint(max(1, next_id - 30), next_id)


def price(rng: random.Random) -> int:
    return rng.randint(85, 115) if rng.random() < 0.15 else rng.randint(96, 104)


if __name__ == "__main__":
    sys.stdout.write("\n".join(generate(int(sys.argv[1]), int(sys.argv[2]))) + "\n")
