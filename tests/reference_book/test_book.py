"""Unit tests for the reference book, mirroring engine/tests/order_types_test.cpp.

Each test feeds canonical command lines and checks the canonical output, so a failure
reads exactly like a differential-test mismatch.
"""

from reference.run import run_lines


def events(*commands: str) -> list[str]:
    """Event lines (no BOOK/LVL lines) produced by the given commands."""
    return [line for line in run_lines(commands) if not line.startswith(("BOOK", "LVL"))]


def last_events(*commands: str) -> list[str]:
    """Events produced by the last command only."""
    seq = commands[-1].split()[1]
    return [line for line in events(*commands) if line.split()[1] == f"seq={seq}"]


def depth(*commands: str) -> list[str]:
    return [line for line in run_lines(commands) if line.startswith("LVL")]


def test_limit_rests_and_book_line_follows_every_command() -> None:
    assert list(run_lines(["N 1 1 1 B LMT GTC 0 CN 100 5"])) == [
        "ACK seq=1 id=1 side=B type=LMT tif=GTC po=0 stp=CN px=100 qty=5",
        "BOOK seq=1 bid=100x5 ask=-",
        "LVL side=B px=100 qty=5 orders=1:5",
    ]


def test_market_sweeps_best_first_and_cancels_remainder() -> None:
    assert last_events(
        "N 1 1 1 S LMT GTC 0 CN 102 3",
        "N 2 2 2 S LMT GTC 0 CN 101 5",
        "N 3 3 3 B MKT IOC 0 CN 0 10",
    ) == [
        "ACK seq=3 id=3 side=B type=MKT tif=IOC po=0 stp=CN px=0 qty=10",
        "TRD seq=3 trade=1 px=101 qty=5 maker=2 taker=3 aggr=B",
        "FILL seq=3 trade=1 id=2 acct=2 side=S liq=M px=101 qty=5 leaves=0 cum=5",
        "FILL seq=3 trade=1 id=3 acct=3 side=B liq=T px=101 qty=5 leaves=5 cum=5",
        "TRD seq=3 trade=2 px=102 qty=3 maker=1 taker=3 aggr=B",
        "FILL seq=3 trade=2 id=1 acct=1 side=S liq=M px=102 qty=3 leaves=0 cum=3",
        "FILL seq=3 trade=2 id=3 acct=3 side=B liq=T px=102 qty=3 leaves=2 cum=8",
        "CXL seq=3 id=3 qty=2 reason=UNFILLED",
    ]


def test_market_into_empty_side_is_rejected() -> None:
    assert last_events("N 1 1 1 B LMT GTC 0 CN 100 5", "N 2 2 2 B MKT IOC 0 CN 0 3") == [
        "REJ seq=2 id=2 reason=NO_LIQUIDITY"
    ]


def test_ioc_that_does_not_cross_is_acked_then_cancelled() -> None:
    assert last_events("N 1 1 1 S LMT GTC 0 CN 101 4", "N 2 2 2 B LMT IOC 0 CN 100 5") == [
        "ACK seq=2 id=2 side=B type=LMT tif=IOC po=0 stp=CN px=100 qty=5",
        "CXL seq=2 id=2 qty=5 reason=UNFILLED",
    ]


def test_fok_one_short_is_rejected_and_book_unchanged() -> None:
    setup = ("N 1 1 1 S LMT GTC 0 CN 100 3", "N 2 2 1 S LMT GTC 0 CN 101 4")
    assert last_events(*setup, "N 3 3 2 B LMT FOK 0 CN 101 8") == [
        "REJ seq=3 id=3 reason=FOK_NOT_FILLABLE"
    ]
    assert depth(*setup, "N 3 3 2 B LMT FOK 0 CN 101 8") == depth(*setup)


def test_fok_availability_follows_stp_mode() -> None:
    setup = (
        "N 1 1 1 S LMT GTC 0 CN 100 2",
        "N 2 2 2 S LMT GTC 0 CN 100 3",
        "N 3 3 1 S LMT GTC 0 CN 100 5",
    )
    for mode in ("CN", "CB"):
        assert last_events(*setup, f"N 4 4 2 B LMT FOK 0 {mode} 100 6") == [
            "REJ seq=4 id=4 reason=FOK_NOT_FILLABLE"
        ]
    filled = last_events(*setup, "N 4 4 2 B LMT FOK 0 CO 100 6")
    assert "CXL seq=4 id=2 qty=3 reason=SELF_TRADE" in filled
    assert filled[-1] == "FILL seq=4 trade=2 id=4 acct=2 side=B liq=T px=100 qty=4 leaves=0 cum=6"
    # Rejected under cancel-oldest: own order 2 survives.
    assert depth(*setup, "N 4 4 2 B LMT FOK 0 CO 100 8") == depth(*setup)


def test_post_only_rests_or_rejects_without_repricing() -> None:
    setup = ("N 1 1 1 S LMT GTC 0 CN 101 5",)
    assert last_events(*setup, "N 2 2 2 B LMT GTC 1 CN 100 4") == [
        "ACK seq=2 id=2 side=B type=LMT tif=GTC po=1 stp=CN px=100 qty=4"
    ]
    for px in (101, 105):
        assert last_events(*setup, f"N 2 2 2 B LMT GTC 1 CN {px} 4") == [
            "REJ seq=2 id=2 reason=POST_ONLY_WOULD_CROSS"
        ]
    # Crossing its own account's order is still a cross.
    assert last_events(*setup, "N 2 2 1 B LMT GTC 1 CN 101 1") == [
        "REJ seq=2 id=2 reason=POST_ONLY_WOULD_CROSS"
    ]


def test_invalid_type_combinations() -> None:
    for line in (
        "N 1 1 1 B MKT GTC 0 CN 0 5",
        "N 1 1 1 B MKT FOK 0 CN 0 5",
        "N 1 1 1 B MKT IOC 1 CN 0 5",
        "N 1 1 1 B LMT IOC 1 CN 100 5",
        "N 1 1 1 B LMT FOK 1 CN 100 5",
    ):
        assert events(line) == ["REJ seq=1 id=1 reason=INVALID_ORDER_TYPE"], line


def test_validation_order() -> None:
    assert events("N 1 1 1 B MKT GTC 0 CN 5 0") == ["REJ seq=1 id=1 reason=INVALID_PRICE"]
    assert events("N 1 1 1 B MKT GTC 0 CN 0 0") == ["REJ seq=1 id=1 reason=INVALID_QTY"]
    assert last_events("N 1 1 1 B LMT GTC 0 CN 100 5", "N 2 1 1 S MKT GTC 0 CN 0 5") == [
        "REJ seq=2 id=1 reason=INVALID_ORDER_TYPE"
    ]
    assert last_events("N 1 1 1 B LMT GTC 0 CN 100 5", "N 2 1 2 B MKT IOC 0 CN 0 1") == [
        "REJ seq=2 id=1 reason=DUPLICATE_ORDER_ID"
    ]


def test_replace_priority_rules() -> None:
    setup = (
        "N 1 1 1 B LMT GTC 0 CN 100 5",
        "N 2 2 2 B LMT GTC 0 CN 100 5",
        "N 3 3 3 B LMT GTC 0 CN 100 5",
    )
    assert last_events(*setup, "R 4 1 100 3") == ["RPL seq=4 id=1 px=100 qty=3 leaves=3 prio=KEPT"]
    assert depth(*setup, "R 4 1 100 3") == ["LVL side=B px=100 qty=13 orders=1:3,2:5,3:5"]
    assert depth(*setup, "R 4 1 100 5") == ["LVL side=B px=100 qty=15 orders=1:5,2:5,3:5"]
    assert depth(*setup, "R 4 1 100 7") == ["LVL side=B px=100 qty=17 orders=2:5,3:5,1:7"]
    assert depth(*setup, "N 4 4 4 B LMT GTC 0 CN 99 2", "R 5 1 99 5") == [
        "LVL side=B px=100 qty=10 orders=2:5,3:5",
        "LVL side=B px=99 qty=7 orders=4:2,1:5",
    ]


def test_replace_price_change_crosses_and_continues_cum_qty() -> None:
    assert last_events(
        "N 1 1 1 S LMT GTC 0 CN 102 4",
        "N 2 2 2 S LMT GTC 0 CN 103 3",
        "N 3 3 3 B LMT GTC 0 CN 100 10",
        "N 4 4 4 S LMT GTC 0 CN 100 2",
        "R 5 3 103 12",
    ) == [
        "RPL seq=5 id=3 px=103 qty=12 leaves=10 prio=LOST",
        "TRD seq=5 trade=2 px=102 qty=4 maker=1 taker=3 aggr=B",
        "FILL seq=5 trade=2 id=1 acct=1 side=S liq=M px=102 qty=4 leaves=0 cum=4",
        "FILL seq=5 trade=2 id=3 acct=3 side=B liq=T px=102 qty=4 leaves=6 cum=6",
        "TRD seq=5 trade=3 px=103 qty=3 maker=2 taker=3 aggr=B",
        "FILL seq=5 trade=3 id=2 acct=2 side=S liq=M px=103 qty=3 leaves=0 cum=3",
        "FILL seq=5 trade=3 id=3 acct=3 side=B liq=T px=103 qty=3 leaves=3 cum=9",
    ]


def test_replace_rejects() -> None:
    setup = ("N 1 1 1 B LMT GTC 0 CN 100 10", "N 2 2 2 S LMT GTC 0 CN 100 4")
    assert last_events(*setup, "R 3 1 100 4") == ["REJ seq=3 id=1 reason=REPLACE_QTY_TOO_LOW"]
    assert last_events(*setup, "R 3 2 100 5") == ["REJ seq=3 id=2 reason=UNKNOWN_ORDER"]
    assert last_events(*setup, "R 3 9 0 5") == ["REJ seq=3 id=9 reason=INVALID_PRICE"]
    assert last_events(*setup, "R 3 9 100 0") == ["REJ seq=3 id=9 reason=INVALID_QTY"]
    assert last_events(
        "N 1 1 1 S LMT GTC 0 CN 105 5", "N 2 2 2 B LMT GTC 1 CN 101 2", "R 3 2 105 2"
    ) == ["REJ seq=3 id=2 reason=POST_ONLY_WOULD_CROSS"]


def test_replace_uses_stored_stp_mode() -> None:
    assert last_events(
        "N 1 1 7 S LMT GTC 0 CN 101 5", "N 2 2 7 B LMT GTC 0 CN 100 3", "R 3 2 101 3"
    ) == ["RPL seq=3 id=2 px=101 qty=3 leaves=3 prio=LOST", "CXL seq=3 id=2 qty=3 reason=SELF_TRADE"]


def test_stp_cancel_newest_mid_sweep() -> None:
    out = last_events(
        "N 1 1 1 S LMT GTC 0 CN 100 3",
        "N 2 2 2 S LMT GTC 0 CN 101 2",
        "N 3 3 3 S LMT GTC 0 CN 101 4",
        "N 4 4 3 B LMT GTC 0 CN 102 10",
    )
    assert out[-1] == "CXL seq=4 id=4 qty=5 reason=SELF_TRADE"
    assert sum(1 for line in out if line.startswith("TRD")) == 2


def test_stp_cancel_oldest_continues_and_rests() -> None:
    commands = (
        "N 1 1 2 S LMT GTC 0 CN 100 2",
        "N 2 2 1 S LMT GTC 0 CN 100 3",
        "N 3 3 2 B LMT GTC 0 CO 100 4",
    )
    assert last_events(*commands) == [
        "ACK seq=3 id=3 side=B type=LMT tif=GTC po=0 stp=CO px=100 qty=4",
        "CXL seq=3 id=1 qty=2 reason=SELF_TRADE",
        "TRD seq=3 trade=1 px=100 qty=3 maker=2 taker=3 aggr=B",
        "FILL seq=3 trade=1 id=2 acct=1 side=S liq=M px=100 qty=3 leaves=0 cum=3",
        "FILL seq=3 trade=1 id=3 acct=2 side=B liq=T px=100 qty=3 leaves=1 cum=3",
    ]
    assert depth(*commands) == ["LVL side=B px=100 qty=1 orders=3:1"]


def test_stp_cancel_both() -> None:
    assert last_events(
        "N 1 1 7 B LMT GTC 0 CN 100 5", "N 2 2 7 S MKT IOC 0 CB 0 3"
    ) == [
        "ACK seq=2 id=2 side=S type=MKT tif=IOC po=0 stp=CB px=0 qty=3",
        "CXL seq=2 id=1 qty=5 reason=SELF_TRADE",
        "CXL seq=2 id=2 qty=3 reason=SELF_TRADE",
    ]


def test_user_cancel_and_unknown_cancel() -> None:
    assert last_events("N 1 1 1 B LMT GTC 0 CN 100 5", "C 2 1") == [
        "CXL seq=2 id=1 qty=5 reason=USER"
    ]
    assert events("C 1 9") == ["REJ seq=1 id=9 reason=UNKNOWN_ORDER"]
