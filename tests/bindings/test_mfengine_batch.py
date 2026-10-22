"""process_batch (M7): per-command offsets, errors in the middle of a batch, the GIL
released while the batch runs, and the guard against a second thread using the engine."""

from __future__ import annotations

import threading

import pytest

import mfengine as mf
from mf_helpers import batch_of, built_commands
from reference.generator import generate

B, S = mf.Side.BUY, mf.Side.SELL


def order(seq: int, oid: int, side: mf.Side, px: int, qty: int, acct: int = 1) -> mf.NewOrder:
    return mf.NewOrder(seq_no=seq, order_id=oid, account_id=acct, side=side, price=px, qty=qty)


def test_offsets_index_each_commands_events() -> None:
    commands = [order(1, 1, S, 101, 5), mf.Cancel(seq_no=2, order_id=99), order(3, 2, B, 101, 2, 2)]
    result = mf.Engine().process_batch(batch_of((c, 0) for c in commands))
    assert result.command_count == 3
    assert result.offsets == [0, 1, 2, 6]
    assert len(result) == 6
    assert [type(e) for e in result.events_of(2)] == [mf.Ack, mf.Trade, mf.Fill, mf.Fill]
    assert result[-1] == result.events_of(2)[-1]
    assert isinstance(result[1], mf.Reject)
    with pytest.raises(IndexError):
        result[6]
    with pytest.raises(IndexError):
        result.events_of(3)


def test_empty_batch() -> None:
    result = mf.Engine().process_batch(mf.CommandBatch())
    assert (len(result), result.command_count, result.offsets, list(result)) == (0, 0, [0], [])


def test_command_batch_is_reusable() -> None:
    batch = mf.CommandBatch()
    batch.append(order(1, 1, B, 100, 5), timestamp=10)
    batch.extend([(order(2, 2, B, 99, 5), 20)])
    assert len(batch) == 2
    first = mf.Engine().process_batch(batch)
    second = mf.Engine().process_batch(batch)
    assert list(first) == list(second)
    batch.clear()
    assert len(batch) == 0


def test_batch_events_outlive_the_engine() -> None:
    engine = mf.Engine()
    result = engine.process_batch(batch_of([(order(1, 1, B, 100, 5), 0)]))
    del engine
    assert str(result[0]).startswith("ACK seq=1")


def test_a_sequence_error_mid_batch_keeps_the_engine_usable() -> None:
    commands = [order(1, 1, B, 100, 5), order(2, 2, B, 99, 5), order(2, 3, B, 98, 5),
                order(4, 4, B, 97, 5)]
    engine = mf.Engine()
    with pytest.raises(mf.SequenceError) as info:
        engine.process_batch(batch_of((c, 0) for c in commands))
    err = info.value
    assert isinstance(err, mf.InvariantViolation)
    assert err.command_index == 2
    assert err.events.command_count == 2
    assert [e.order_id for e in err.events] == [1, 2]
    # Commands before the bad one were applied, nothing else was.
    assert not engine.failed
    assert engine.last_seq_no == 2
    assert engine.depth(B) == [(100, 5, 1), (99, 5, 1)]
    assert engine.process(order(3, 3, B, 98, 5))[0].order_id == 3


def test_a_backwards_timestamp_mid_batch_is_a_sequence_error() -> None:
    engine = mf.Engine()
    with pytest.raises(mf.SequenceError) as info:
        engine.process_batch(batch_of([(order(1, 1, B, 100, 5), 50), (order(2, 2, B, 99, 5), 49)]))
    assert info.value.command_index == 1
    assert not engine.failed
    engine.process(order(2, 2, B, 99, 5), timestamp=50)


def test_a_failure_after_state_changed_fails_the_engine_mid_batch() -> None:
    # The fault hook makes the engine raise InvariantViolation after its next command
    # has been applied, as the post-command invariant walk would.
    engine = mf.Engine()
    engine.process(order(1, 1, B, 100, 5))
    mf._core._inject_fault_after_next_command(engine)
    batch = batch_of([(order(2, 2, B, 99, 5), 0), (order(3, 3, B, 98, 5), 0)])
    with pytest.raises(mf.InvariantViolation) as info:
        engine.process_batch(batch)
    assert not isinstance(info.value, mf.SequenceError)
    assert info.value.command_index == 0
    assert info.value.events.command_count == 0
    assert engine.failed


def test_gil_is_released_while_a_batch_runs() -> None:
    # Another thread can only see the engine busy if it runs while the batch is inside
    # C++, which needs the GIL released. The guard then refuses the concurrent call.
    commands = built_commands(generate(20261020, 200_000))
    batch = batch_of((c, 0) for c in commands)
    engine = mf.Engine()
    started = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        started.set()
        try:
            engine.process_batch(batch)
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    worker = threading.Thread(target=run)
    worker.start()
    started.wait()
    refused = 0
    while worker.is_alive():
        try:
            engine.best_bid()
        except RuntimeError as e:
            assert "in use" in str(e)
            refused += 1
    worker.join()
    assert not errors
    assert refused > 0
    assert engine.last_seq_no == commands[-1].seq_no
    assert not engine.failed
