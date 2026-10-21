import pytest

from reference.commands import Cancel, NewOrder, Replace, format_command, parse_command


def test_parses_new_order() -> None:
    assert parse_command("N 7 42 3 S MKT IOC 0 CB 0 15") == NewOrder(
        seq=7, order_id=42, account=3, side="S", type="MKT", tif="IOC",
        post_only=False, stp="CB", price=0, qty=15,
    )


def test_parses_cancel_and_replace() -> None:
    assert parse_command("C 3 9") == Cancel(seq=3, order_id=9)
    assert parse_command("R 4 9 101 12") == Replace(seq=4, order_id=9, price=101, qty=12)


def test_parses_negative_price_and_qty() -> None:
    cmd = parse_command("N 1 1 1 B LMT FOK 1 CN -5 -3")
    assert isinstance(cmd, NewOrder)
    assert (cmd.price, cmd.qty, cmd.post_only) == (-5, -3, True)


@pytest.mark.parametrize("line", ["", "   ", "# seed=1", "  # indented comment"])
def test_blank_and_comment_lines_are_skipped(line: str) -> None:
    assert parse_command(line) is None


def test_trailing_comment_is_ignored() -> None:
    assert parse_command("C 3 9  # note") == Cancel(seq=3, order_id=9)


@pytest.mark.parametrize(
    "line",
    [
        "X 1 2",
        "C 1",
        "C 1 2 3",
        "C 1 x",
        "C -1 2",
        "N 1 1 1 Q LMT GTC 0 CN 100 5",
        "N 1 1 1 B STP GTC 0 CN 100 5",
        "N 1 1 1 B LMT DAY 0 CN 100 5",
        "N 1 1 1 B LMT GTC 2 CN 100 5",
        "N 1 1 1 B LMT GTC 0 XX 100 5",
        "R 1 1 100 5x",
    ],
)
def test_malformed_lines_raise(line: str) -> None:
    with pytest.raises(ValueError):
        parse_command(line)


@pytest.mark.parametrize(
    "line",
    ["N 7 42 3 S MKT IOC 0 CB 0 15", "N 1 2 3 B LMT GTC 1 CO 100 -4", "C 3 9", "R 4 9 101 12"],
)
def test_format_round_trips(line: str) -> None:
    cmd = parse_command(line)
    assert cmd is not None
    assert format_command(cmd) == line
