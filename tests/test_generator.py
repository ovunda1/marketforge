from collections import Counter

from reference.commands import Cancel, NewOrder, Replace, parse_command
from reference.generator import generate


def test_same_seed_same_commands() -> None:
    assert generate(7, 2000) == generate(7, 2000)


def test_different_seeds_differ() -> None:
    assert generate(7, 2000) != generate(8, 2000)


def test_header_records_seed_and_count() -> None:
    lines = generate(123, 10)
    assert lines[0].startswith("# ")
    assert "seed=123" in lines[0] and "n=10" in lines[0]


def test_emits_exactly_n_commands_with_increasing_seq() -> None:
    commands = [c for c in map(parse_command, generate(5, 5000)) if c is not None]
    assert len(commands) == 5000
    assert [c.seq for c in commands] == list(range(1, 5001))


def test_covers_every_command_shape() -> None:
    commands = [c for c in map(parse_command, generate(1, 20_000)) if c is not None]
    kinds: Counter[str] = Counter()
    for c in commands:
        if isinstance(c, Cancel):
            kinds["cancel"] += 1
        elif isinstance(c, Replace):
            kinds["replace"] += 1
        elif isinstance(c, NewOrder):
            kinds[f"{c.type}-{c.tif}-po{int(c.post_only)}"] += 1
            kinds[f"stp-{c.stp}"] += 1
            if c.qty <= 0 or (c.type == "MKT" and c.price != 0) or (c.type == "LMT" and c.price <= 0):
                kinds["invalid-field"] += 1
    for kind in (
        "cancel", "replace", "LMT-GTC-po0", "LMT-GTC-po1", "LMT-IOC-po0", "LMT-FOK-po0",
        "MKT-IOC-po0", "MKT-GTC-po0", "stp-CN", "stp-CO", "stp-CB", "invalid-field",
    ):
        assert kinds[kind] > 0, kind


def test_reuses_ids_of_orders_that_are_gone() -> None:
    # Exercises session-wide id uniqueness (M4): new orders with ids that were cancelled.
    cancelled: set[int] = set()
    reused = 0
    for c in map(parse_command, generate(1, 20_000)):
        if isinstance(c, Cancel):
            cancelled.add(c.order_id)
        elif isinstance(c, NewOrder) and c.order_id in cancelled:
            reused += 1
    assert reused > 50


# --- Wide profile (M6) -----------------------------------------------------------------
# The narrow stream is what every earlier difftest ran: adding a profile must not change it.
NARROW_5000_SHA256 = "1e9248a5f9727a07f3868fd534afa9651faf7e0fe8415eec39dec70c028193b7"


def _sha(lines: list[str]) -> str:
    import hashlib

    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def test_narrow_stream_is_unchanged() -> None:
    assert _sha(generate(20261020, 5000)) == NARROW_5000_SHA256
    assert generate(20261020, 5000, profile="narrow") == generate(20261020, 5000)


def test_wide_profile_is_deterministic_and_differs() -> None:
    assert generate(7, 2000, profile="wide") == generate(7, 2000, profile="wide")
    assert generate(7, 2000, profile="wide") != generate(7, 2000)
    assert "profile=wide" in generate(7, 10, profile="wide")[0]


def test_unknown_profile_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        generate(1, 10, profile="medium")


def _limit_prices(lines: list[str]) -> list[int]:
    return [c.price for c in map(parse_command, lines)
            if isinstance(c, NewOrder) and c.type == "LMT" and c.price > 0]


def test_wide_profile_spreads_prices_and_drifts() -> None:
    lines = generate(3, 50_000, profile="wide")
    prices = _limit_prices(lines)
    assert max(prices) - min(prices) > 20_000  # far-away orders
    assert min(prices) <= 3 and max(prices) >= 10**15  # the edges of the price range
    # Most prices sit near a mid that moves: compare the medians of the first and last
    # fifths of the run.
    fifth = len(prices) // 5
    first, last = sorted(prices[:fifth]), sorted(prices[-fifth:])
    assert abs(first[len(first) // 2] - last[len(last) // 2]) > 50
    narrow = _limit_prices(generate(3, 50_000))
    assert max(narrow) - min(narrow) <= 2 * 8


def test_wide_profile_covers_every_command_shape() -> None:
    commands = [c for c in map(parse_command, generate(1, 20_000, profile="wide")) if c is not None]
    assert sum(isinstance(c, Cancel) for c in commands) > 1000
    assert sum(isinstance(c, Replace) for c in commands) > 1000
    kinds = {f"{c.type}-{c.tif}-po{int(c.post_only)}" for c in commands if isinstance(c, NewOrder)}
    assert {"LMT-GTC-po0", "LMT-GTC-po1", "LMT-IOC-po0", "LMT-FOK-po0", "MKT-IOC-po0"} <= kinds
