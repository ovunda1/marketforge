"""Seeded random command generator for the differential test.

The file is written once and consumed by both engines, so nothing about Python's RNG
needs to match C++. The generator is only a stimulus: it keeps a rough belief about
which of its orders may still be resting (it does not simulate fills) so that cancels
and replaces mostly hit live orders, and it cancels orders that get old so the book
stays small enough for the reference engine's linear scans.

Usage: python -m reference.generator --seed S --n N [-o FILE]
"""

from __future__ import annotations

import argparse
import random
import sys

from reference.commands import Cancel, NewOrder, Replace, format_command

GENERATOR_VERSION = 2  # 2: reuses ids of cancelled and rejected orders (M4)
MID = 1000
BAND = 8  # prices within MID +/- BAND ticks, so most orders interact
ACCOUNTS = 4  # few accounts, so self-trades are common
MAX_AGE = 400  # commands after which a possibly-resting order is cancelled


def generate(seed: int, n: int) -> list[str]:
    rng = random.Random(seed)
    lines = [f"# marketforge difftest seed={seed} n={n} generator={GENERATOR_VERSION}"]
    # (seq issued, id, price, total qty) for GTC limits that may still be resting.
    maybe_resting: list[tuple[int, int, int, int]] = []
    # Ids that are likely gone (cancelled) or never accepted (invalid orders), for
    # exercising session-wide id uniqueness: the first are duplicates, the second free.
    retired: list[int] = []
    next_id = 1

    def new_id() -> int:
        nonlocal next_id
        next_id += 1
        return next_id - 1

    def qty() -> int:
        return rng.randint(30, 150) if rng.random() < 0.1 else rng.randint(1, 30)

    def stp() -> str:
        return rng.choices(("CN", "CO", "CB"), weights=(2, 1, 1))[0]

    for seq in range(1, n + 1):
        r = rng.random()
        if maybe_resting and seq - maybe_resting[0][0] > MAX_AGE:
            _, oid, _, _ = maybe_resting.pop(0)
            retired.append(oid)
            cmd: NewOrder | Cancel | Replace = Cancel(seq, oid)
        elif r < 0.18 and maybe_resting:
            _, oid, _, _ = maybe_resting.pop(rng.randrange(len(maybe_resting)))
            retired.append(oid)
            cmd = Cancel(seq, oid)
        elif r < 0.20:
            cmd = Cancel(seq, rng.randint(1, next_id + 5))  # often unknown
        elif r < 0.34 and maybe_resting:
            i = rng.randrange(len(maybe_resting))
            issued, oid, px, total = maybe_resting[i]
            v = rng.random()
            if v < 0.25:
                new_px, new_qty = px, max(1, total - rng.randint(1, 10))  # decrease
            elif v < 0.40:
                new_px, new_qty = px, total  # no-op
            elif v < 0.60:
                new_px, new_qty = px, total + rng.randint(1, 20)  # increase
            elif v < 0.87:
                new_px, new_qty = MID + rng.randint(-BAND, BAND), qty()  # re-price
            elif v < 0.97:
                new_px, new_qty = px, rng.randint(1, 3)  # often at or below filled
            else:
                new_px, new_qty = rng.choice((0, -1, px)), rng.choice((0, -2, total))
            maybe_resting[i] = (issued, oid, new_px, new_qty)
            cmd = Replace(seq, oid, new_px, new_qty)
        elif r < 0.37:
            cmd = _invalid_order(rng, seq, new_id(), maybe_resting, retired)
            retired.append(cmd.order_id)
        else:
            side = rng.choice("BS")
            kind = rng.random()
            # Passive prices sit on their own side of MID (bids below, asks at or above), so
            # depth builds up; aggressive prices reach into the other side.
            aggressive = MID + rng.randint(-2, BAND) if side == "B" else MID - rng.randint(-2, BAND)
            passive = MID - rng.randint(1, BAND) if side == "B" else MID + rng.randint(0, BAND - 1)
            account = rng.randint(1, ACCOUNTS)
            oid = new_id()
            if kind < 0.48:
                cmd = NewOrder(seq, oid, account, side, "LMT", "GTC", False, stp(), passive, qty())
            elif kind < 0.60:
                cmd = NewOrder(seq, oid, account, side, "LMT", "GTC", False, stp(), aggressive, qty())
            elif kind < 0.70:
                cmd = NewOrder(seq, oid, account, side, "LMT", "GTC", True, stp(), passive, qty())
            elif kind < 0.82:
                cmd = NewOrder(seq, oid, account, side, "LMT", "IOC", False, stp(), aggressive, qty())
            elif kind < 0.90:
                cmd = NewOrder(seq, oid, account, side, "LMT", "FOK", False, stp(), aggressive, qty())
            else:
                # Occasionally sweep a whole side, so later market orders find it empty.
                size = rng.randint(1000, 3000) if rng.random() < 0.08 else qty()
                cmd = NewOrder(seq, oid, account, side, "MKT", "IOC", False, stp(), 0, size)
            if cmd.tif == "GTC":
                maybe_resting.append((seq, oid, cmd.price, cmd.qty))
        lines.append(format_command(cmd))
    return lines


def _invalid_order(
    rng: random.Random,
    seq: int,
    oid: int,
    maybe_resting: list[tuple[int, int, int, int]],
    retired: list[int],
) -> NewOrder:
    side = rng.choice("BS")
    account = rng.randint(1, ACCOUNTS)
    px = MID + rng.randint(-BAND, BAND)
    v = rng.randrange(8)
    if v == 0:
        return NewOrder(seq, oid, account, side, "LMT", "GTC", False, "CN", rng.choice((0, -5)), 5)
    if v == 1:
        return NewOrder(seq, oid, account, side, "LMT", "GTC", False, "CN", px, rng.choice((0, -3)))
    if v == 2:
        return NewOrder(seq, oid, account, side, "MKT", "IOC", False, "CN", px, 5)
    if v == 3:
        return NewOrder(seq, oid, account, side, "MKT", rng.choice(("GTC", "FOK")), False, "CN", 0, 5)
    if v == 4:
        return NewOrder(seq, oid, account, side, "MKT", "IOC", True, "CN", 0, 5)
    if v == 5:
        return NewOrder(seq, oid, account, side, "LMT", rng.choice(("IOC", "FOK")), True, "CN", px, 5)
    # Reuse an id that may still be resting, or one that is gone: DUPLICATE_ORDER_ID if
    # it was ever acked, accepted if its order was rejected.
    if retired and (not maybe_resting or rng.random() < 0.5):
        dup = rng.choice(retired)
    else:
        dup = maybe_resting[rng.randrange(len(maybe_resting))][1] if maybe_resting else oid
    tif = rng.choice(("IOC", "FOK", "IOC"))
    if v == 6:
        return NewOrder(seq, dup, account, side, "LMT", tif, False, "CN", px, 5)
    return NewOrder(seq, dup, account, side, "MKT", "IOC", False, "CN", 0, 5)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--n", type=int, required=True)
    parser.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    text = "\n".join(generate(args.seed, args.n)) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
