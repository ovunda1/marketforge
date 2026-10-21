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
