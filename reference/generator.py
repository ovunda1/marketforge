"""Seeded random command generator for the differential test.

The file is written once and consumed by both engines, so nothing about Python's RNG
needs to match C++. The generator is only a stimulus: it keeps a rough belief about
which of its orders may still be resting (it does not simulate fills) so that cancels
and replaces mostly hit live orders, and it cancels orders that get old so the book
stays small enough for the reference engine's linear scans.

Profiles: "narrow" (the default, every difftest before M6) keeps prices within a few
ticks of a fixed mid. "wide" (M6) lets the mid drift and spreads prices over thousands of
ticks, with a few orders far away: many shallow levels, prices that leave any window
around the mid, the cases a flat tick array handles differently from a sorted map.

Usage: python -m reference.generator --seed S --n N [--profile narrow|wide] [-o FILE]
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
PROFILES = ("narrow", "wide")
# Wide profile: the mid starts here and drifts within these bounds.
WIDE_MID, WIDE_MIN_MID, WIDE_MAX_MID = 100_000, 90_000, 110_000


def generate(seed: int, n: int, profile: str = "narrow") -> list[str]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    wide = profile == "wide"
    rng = random.Random(seed)
    header = f"# marketforge difftest seed={seed} n={n} generator={GENERATOR_VERSION}"
    lines = [header + (" profile=wide" if wide else "")]
    mid = WIDE_MID if wide else MID

    def wide_offset() -> int:
        # Log-uniform up to 2048 ticks, and one order in a hundred far from the mid.
        if rng.random() < 0.01:
            return rng.randint(10_000, 30_000)
        return 1 + rng.randrange(1 << rng.randrange(12))

    def wide_passive(side: str) -> int:
        # One in five hundred at an extreme price: the edges of the price range.
        if rng.random() < 0.002:
            return rng.choice((1, 2, 3, 10**9, 10**15, 4 * 10**18))
        return mid - wide_offset() if side == "B" else mid + wide_offset() - 1

    def reprice() -> int:
        if not wide:
            return MID + rng.randint(-BAND, BAND)
        return mid + rng.choice((-1, 1)) * wide_offset()

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
        if wide and seq % 8 == 0:
            mid = min(max(mid + rng.randint(-4, 4), WIDE_MIN_MID), WIDE_MAX_MID)
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
                new_px, new_qty = reprice(), qty()  # re-price
            elif v < 0.97:
                new_px, new_qty = px, rng.randint(1, 3)  # often at or below filled
            else:
                new_px, new_qty = rng.choice((0, -1, px)), rng.choice((0, -2, total))
            maybe_resting[i] = (issued, oid, new_px, new_qty)
            cmd = Replace(seq, oid, new_px, new_qty)
        elif r < 0.37:
            cmd = _invalid_order(rng, seq, new_id(), maybe_resting, retired, mid, wide)
            retired.append(cmd.order_id)
        else:
            side = rng.choice("BS")
            kind = rng.random()
            # Passive prices sit on their own side of MID (bids below, asks at or above), so
            # depth builds up; aggressive prices reach into the other side.
            if wide:
                reach = 20
                aggressive = mid + rng.randint(-2, reach) if side == "B" else mid - rng.randint(-2, reach)
                passive = wide_passive(side)
            else:
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
    mid: int = MID,
    wide: bool = False,
) -> NewOrder:
    side = rng.choice("BS")
    account = rng.randint(1, ACCOUNTS)
    px = mid + rng.randint(-20, 20) if wide else MID + rng.randint(-BAND, BAND)
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
    parser.add_argument("--profile", choices=PROFILES, default="narrow")
    parser.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    text = "\n".join(generate(args.seed, args.n, args.profile)) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
