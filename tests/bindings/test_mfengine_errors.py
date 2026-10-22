"""Errors from Python (M7): C++ exceptions map to mfengine exception classes; a contract
error found before any state change (SequenceError) leaves the engine usable, while a
failure after the engine began changing state fails it for good (EngineFailed); and
integers out of range raise instead of wrapping."""

from __future__ import annotations

import pytest

import mfengine as mf

B = mf.Side.BUY


def order(seq: int, oid: int, px: int = 100, qty: int = 5) -> mf.NewOrder:
    return mf.NewOrder(seq_no=seq, order_id=oid, account_id=1, side=B, price=px, qty=qty)


def test_exception_hierarchy() -> None:
    for exc in (mf.InvariantViolation, mf.SequenceError, mf.EngineFailed, mf.JournalError,
                mf.SnapshotError, mf.ConfigError, mf.ParseError):
        assert issubclass(exc, mf.MarketForgeError), exc
    assert issubclass(mf.MarketForgeError, Exception)
    assert issubclass(mf.SequenceError, mf.InvariantViolation)
    assert not issubclass(mf.EngineFailed, mf.InvariantViolation)


# --- Detected before any state change: the engine stays usable ---------------------------


def test_stale_seq_no_raises_sequence_error_and_changes_nothing() -> None:
    engine = mf.Engine()
    engine.process(order(1, 1))
    engine.process(order(2, 2, px=99))
    before = engine.snapshot()
    for stale in (order(2, 3), mf.Cancel(seq_no=1, order_id=1),
                  mf.SetSession(seq_no=2, state=mf.SessionState.HALTED)):
        with pytest.raises(mf.SequenceError, match="does not exceed"):
            engine.process(stale)
        assert not engine.failed
        assert engine.snapshot() == before
    assert engine.process(order(3, 3, px=98))[0].order_id == 3


def test_backwards_timestamp_raises_sequence_error_and_changes_nothing() -> None:
    engine = mf.Engine()
    engine.process(order(1, 1), timestamp=1_000)
    before = engine.snapshot()
    with pytest.raises(mf.SequenceError, match="before"):
        engine.process(mf.Cancel(seq_no=2, order_id=1), timestamp=999)
    assert not engine.failed
    assert engine.snapshot() == before
    assert engine.last_timestamp == 1_000
    engine.process(mf.Cancel(seq_no=2, order_id=1), timestamp=1_000)


# --- Thrown after the engine began changing state: the engine is failed ------------------


def failed_engine() -> mf.Engine:
    engine = mf.Engine()
    engine.process(order(1, 1))
    mf._core._inject_fault_after_next_command(engine)
    with pytest.raises(mf.InvariantViolation, match="injected") as info:
        engine.process(order(2, 2, px=99))
    assert not isinstance(info.value, mf.SequenceError)
    return engine


def test_a_failure_after_state_changed_fails_the_engine() -> None:
    engine = failed_engine()
    assert engine.failed
    with pytest.raises(mf.EngineFailed, match="injected"):  # names the original failure
        engine.process(order(3, 3, px=98))


@pytest.mark.parametrize("call", [
    lambda e: e.process(mf.Cancel(seq_no=9, order_id=1)),
    lambda e: e.process_batch(mf.CommandBatch()),
    lambda e: e.best_bid(),
    lambda e: e.best_ask(),
    lambda e: e.depth(B),
    lambda e: e.level_orders(B, 100),
    lambda e: e.order(1),
    lambda e: e.order_count,
    lambda e: e.last_seq_no,
    lambda e: e.last_timestamp,
    lambda e: e.session,
    lambda e: e.position(1),
    lambda e: e.positions(),
    lambda e: e.exposure(1),
    lambda e: e.check_invariants(),
    lambda e: e.snapshot(),
    lambda e: mf.format_top_of_book(e, 1),
    lambda e: mf.format_depth(e),
], ids=lambda f: "")
def test_every_later_call_on_a_failed_engine_raises_engine_failed(call) -> None:
    engine = failed_engine()
    with pytest.raises(mf.EngineFailed):
        call(engine)
    assert engine.failed  # still readable


def test_a_failed_engine_does_not_affect_others() -> None:
    failed_engine()
    assert mf.Engine().process(order(1, 1))[0].order_id == 1


# --- Integers -------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("price", 2**63), ("price", -(2**63) - 1), ("qty", 2**64), ("order_id", -1),
    ("order_id", 2**64), ("account_id", -1), ("seq_no", 2**64), ("seq_no", -1),
])
def test_out_of_range_ints_raise_overflow_error(field: str, value: int) -> None:
    fields = dict(seq_no=1, order_id=1, account_id=1, side=B, price=100, qty=5)
    fields[field] = value
    with pytest.raises(OverflowError, match=field):
        mf.NewOrder(**fields)


def test_ints_at_the_limits_are_accepted() -> None:
    o = mf.NewOrder(seq_no=2**64 - 1, order_id=2**64 - 1, account_id=0, side=B,
                    price=-(2**63), qty=2**63 - 1)
    assert (o.seq_no, o.order_id, o.price, o.qty) == (2**64 - 1, 2**64 - 1, -(2**63), 2**63 - 1)


@pytest.mark.parametrize("value", [1.5, 100.0, True, "100", None])
def test_non_int_prices_raise_type_error(value: object) -> None:
    with pytest.raises(TypeError, match="price"):
        mf.NewOrder(seq_no=1, order_id=1, account_id=1, side=B, price=value, qty=5)  # type: ignore[arg-type]


def test_other_int_arguments_are_checked_too() -> None:
    engine = mf.Engine()
    with pytest.raises(OverflowError, match="timestamp"):
        engine.process(order(1, 1), timestamp=2**63)
    with pytest.raises(OverflowError, match="account_id"):
        engine.position(-1)
    with pytest.raises(OverflowError, match="max_qty"):
        mf.InstrumentLimits(max_qty=2**63)
    with pytest.raises(TypeError, match="per_sec"):
        mf.RateLimit(per_sec=1.0, burst=1)  # type: ignore[arg-type]
    with pytest.raises(OverflowError, match="account_id"):
        mf.KillSwitch(seq_no=1, account_id=-1, engaged=True)
    assert engine.last_seq_no is None  # nothing reached the engine


def test_enum_fields_require_the_enum() -> None:
    with pytest.raises(TypeError):
        mf.NewOrder(seq_no=1, order_id=1, account_id=1, side=0, price=100, qty=5)  # type: ignore[arg-type]
