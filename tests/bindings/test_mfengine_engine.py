"""mfengine.Engine from Python (M7): typed events, book and risk queries, admin commands."""

from __future__ import annotations

import enum

import pytest

import mfengine as mf

B, S = mf.Side.BUY, mf.Side.SELL


class Flow:
    """An engine plus a seq_no counter: one helper per command."""

    def __init__(self) -> None:
        self.engine = mf.Engine()
        self.seq = 0
        self.now = 0

    def next(self) -> int:
        self.seq += 1
        return self.seq

    def send(self, command: mf.Command) -> list[mf.Event]:
        return self.engine.process(command, timestamp=self.now)

    def limit(self, oid: int, acct: int, side: mf.Side, px: int, qty: int, **kw) -> list[mf.Event]:
        return self.send(mf.NewOrder(seq_no=self.next(), order_id=oid, account_id=acct, side=side,
                                     price=px, qty=qty, **kw))

    def cancel(self, oid: int) -> list[mf.Event]:
        return self.send(mf.Cancel(seq_no=self.next(), order_id=oid))

    def replace(self, oid: int, px: int, qty: int) -> list[mf.Event]:
        return self.send(mf.Replace(seq_no=self.next(), order_id=oid, price=px, qty=qty))


@pytest.fixture
def flow() -> Flow:
    return Flow()


def test_module_reports_its_build() -> None:
    assert isinstance(mf.INVARIANT_CHECKS, bool)
    assert isinstance(mf.BUILD_TYPE, str) and mf.BUILD_TYPE
    assert mf.version()


def test_enums_are_python_enums() -> None:
    for e in (mf.Side, mf.OrderType, mf.TimeInForce, mf.SessionState, mf.StpMode, mf.RejectReason,
              mf.CancelReason, mf.AdminKind, mf.AdminRejectReason, mf.Liquidity, mf.JournalErrorKind):
        assert issubclass(e, enum.Enum), e
    assert mf.RejectReason.PRICE_BAND.name == "PRICE_BAND"


def test_a_resting_order_is_acked_with_its_fields(flow: Flow) -> None:
    (ack,) = flow.limit(7, 3, B, 100, 5, tif=mf.TimeInForce.DAY)
    assert ack == mf.Ack(seq_no=1, order_id=7, side=B, type=mf.OrderType.LIMIT,
                         tif=mf.TimeInForce.DAY, post_only=False, stp=mf.StpMode.CANCEL_NEWEST,
                         price=100, qty=5)
    assert type(ack) is mf.Ack
    assert str(ack) == "ACK seq=1 id=7 side=B type=LMT tif=DAY po=0 stp=CN px=100 qty=5"
    assert repr(ack).startswith("Ack(seq_no=1, order_id=7, side=")


def test_a_match_gives_trade_and_fills_in_order(flow: Flow) -> None:
    flow.limit(1, 1, S, 101, 5)
    events = flow.limit(2, 2, B, 101, 3)
    assert [type(e) for e in events] == [mf.Ack, mf.Trade, mf.Fill, mf.Fill]
    _, trade, maker, taker = events
    assert (trade.price, trade.qty, trade.maker_order_id, trade.taker_order_id,
            trade.aggressor_side) == (101, 3, 1, 2, B)
    assert (maker.order_id, maker.account_id, maker.liquidity, maker.leaves_qty, maker.cum_qty) == (
        1, 1, mf.Liquidity.MAKER, 2, 3)
    assert (taker.order_id, taker.liquidity, taker.leaves_qty) == (2, mf.Liquidity.TAKER, 0)
    assert maker.trade_id == taker.trade_id == trade.trade_id


def test_events_are_immutable_hashable_values(flow: Flow) -> None:
    (ack,) = flow.limit(1, 1, B, 100, 5)
    with pytest.raises(AttributeError):
        ack.qty = 6  # type: ignore[misc]
    assert hash(ack) == hash(mf.Ack(seq_no=1, order_id=1, side=B, type=mf.OrderType.LIMIT,
                                    tif=mf.TimeInForce.GTC, post_only=False,
                                    stp=mf.StpMode.CANCEL_NEWEST, price=100, qty=5))
    assert ack != mf.Reject(seq_no=1, order_id=1, reason=mf.RejectReason.INVALID_QTY)
    assert ack != "ACK"


def test_events_stay_valid_after_later_commands(flow: Flow) -> None:
    first = flow.limit(1, 1, S, 101, 5)
    kept = [str(e) for e in first]
    for i in range(2, 2_000):  # reuses and grows the engine's event buffer many times
        flow.limit(i, 2, B, 101, 1)
    assert [str(e) for e in first] == kept


def test_rejects_and_cancels_carry_their_reasons(flow: Flow) -> None:
    assert flow.limit(1, 1, B, 100, 0) == [mf.Reject(seq_no=1, order_id=1,
                                                      reason=mf.RejectReason.INVALID_QTY)]
    flow.limit(2, 1, B, 100, 5)
    assert flow.cancel(2) == [mf.Cancelled(seq_no=3, order_id=2, cancelled_qty=5,
                                           reason=mf.CancelReason.USER)]
    (rej,) = flow.cancel(2)
    assert rej.reason is mf.RejectReason.UNKNOWN_ORDER


def test_replace_reports_priority(flow: Flow) -> None:
    flow.limit(1, 1, B, 100, 5)
    (rep,) = flow.replace(1, 100, 3)
    assert rep == mf.Replaced(seq_no=2, order_id=1, price=100, qty=3, leaves_qty=3,
                              priority_kept=True)


def test_book_queries(flow: Flow) -> None:
    assert (flow.engine.best_bid(), flow.engine.best_ask()) == (None, None)
    assert flow.engine.last_seq_no is None
    flow.limit(1, 1, B, 100, 5)
    flow.limit(2, 2, B, 100, 7)
    flow.limit(3, 1, B, 98, 1)
    flow.limit(4, 3, S, 103, 2)
    flow.limit(5, 3, S, 105, 4)
    e = flow.engine
    assert (e.best_bid(), e.best_ask()) == (100, 103)
    assert e.depth(B) == [(100, 12, 2), (98, 1, 1)]
    assert e.depth(S, levels=1) == [(103, 2, 1)]
    assert e.depth(S, levels=0) == []
    assert e.level_orders(B, 100) == [1, 2]
    assert e.level_orders(B, 99) == []
    assert e.order_count == 5
    assert e.last_seq_no == 5
    view = e.order(2)
    assert (view.account_id, view.side, view.price, view.qty, view.leaves_qty, view.filled_qty) == (
        2, B, 100, 7, 7, 0)
    assert e.order(99) is None


def test_positions_and_exposure_follow_fills(flow: Flow) -> None:
    flow.limit(1, 1, S, 101, 5)
    flow.limit(2, 2, B, 102, 3)
    e = flow.engine
    assert e.position(1) == -3 and e.position(2) == 3 and e.position(9) == 0
    assert e.positions() == {1: -3, 2: 3}
    assert list(e.positions()) == [1, 2]  # ascending account id
    x = e.exposure(1)
    assert (x.open_notional, x.open_buy_qty, x.open_sell_qty, x.open_orders) == (202, 0, 2, 1)


def test_exposure_beyond_int64_is_exact(flow: Flow) -> None:
    big = 2**62
    flow.limit(1, 1, B, big, 3)  # notional 3 * 2**62 needs more than 64 bits
    assert flow.engine.exposure(1).open_notional == 3 * big


def test_admin_commands_and_session(flow: Flow) -> None:
    e = flow.engine
    assert e.session is mf.SessionState.CONTINUOUS
    lim = mf.InstrumentLimits(max_qty=10)
    assert flow.send(mf.SetInstrumentLimits(seq_no=flow.next(), limits=lim)) == [
        mf.AdminAck(seq_no=1, kind=mf.AdminKind.INSTRUMENT_LIMITS)]
    (rej,) = flow.limit(1, 1, B, 100, 11)
    assert rej.reason is mf.RejectReason.MAX_QTY

    acct = mf.AccountLimits(max_open_orders=1, rate=mf.RateLimit(per_sec=1, burst=5))
    assert flow.send(mf.SetAccountLimits(seq_no=flow.next(), account_id=1, limits=acct)) == [
        mf.AdminAck(seq_no=3, kind=mf.AdminKind.ACCOUNT_LIMITS)]
    flow.limit(2, 1, B, 100, 1)
    (rej,) = flow.limit(3, 1, B, 100, 1)
    assert rej.reason is mf.RejectReason.MAX_OPEN_ORDERS

    bad = mf.SetInstrumentLimits(seq_no=flow.next(), limits=mf.InstrumentLimits(band_bps=10**9))
    assert flow.send(bad) == [mf.AdminReject(seq_no=6, reason=mf.AdminRejectReason.INVALID_LIMITS)]

    assert flow.send(mf.SetSession(seq_no=flow.next(), state=mf.SessionState.PRE_OPEN)) == [
        mf.AdminReject(seq_no=7, reason=mf.AdminRejectReason.INVALID_TRANSITION)]
    assert flow.send(mf.SetSession(seq_no=flow.next(), state=mf.SessionState.HALTED)) == [
        mf.SessionChanged(seq_no=8, state=mf.SessionState.HALTED)]
    assert e.session is mf.SessionState.HALTED


def test_kill_switch_cancels_open_orders(flow: Flow) -> None:
    flow.limit(1, 1, B, 100, 5)
    flow.limit(2, 2, B, 99, 5)
    events = flow.send(mf.KillSwitch(seq_no=flow.next(), account_id=1, engaged=True,
                                     cancel_open=True))
    assert events == [mf.AdminAck(seq_no=3, kind=mf.AdminKind.KILL_SWITCH),
                      mf.Cancelled(seq_no=3, order_id=1, cancelled_qty=5,
                                   reason=mf.CancelReason.KILL_SWITCH)]
    (rej,) = flow.limit(3, 1, B, 100, 1)
    assert rej.reason is mf.RejectReason.KILL_SWITCH
    assert flow.engine.order(2) is not None


def test_day_orders_expire_at_the_close(flow: Flow) -> None:
    flow.limit(1, 1, B, 100, 5, tif=mf.TimeInForce.DAY)
    flow.limit(2, 1, B, 99, 5)
    events = flow.send(mf.SetSession(seq_no=flow.next(), state=mf.SessionState.CLOSED))
    assert events == [mf.SessionChanged(seq_no=3, state=mf.SessionState.CLOSED),
                      mf.Cancelled(seq_no=3, order_id=1, cancelled_qty=5,
                                   reason=mf.CancelReason.EXPIRED)]


def test_commands_have_defaults_and_renumber() -> None:
    order = mf.NewOrder(seq_no=0, order_id=1, account_id=2, side=S, price=100, qty=3)
    assert (order.type, order.tif, order.post_only, order.stp) == (
        mf.OrderType.LIMIT, mf.TimeInForce.GTC, False, mf.StpMode.CANCEL_NEWEST)
    moved = order.with_seq_no(42)
    assert moved.seq_no == 42 and order.seq_no == 0
    assert moved == mf.NewOrder(seq_no=42, order_id=1, account_id=2, side=S, price=100, qty=3)
    with pytest.raises(AttributeError):
        order.qty = 4  # type: ignore[misc]
    limits = mf.AccountLimits(credit=5)
    assert (limits.max_notional, limits.credit, limits.rate) == (None, 5, None)
    kill = mf.KillSwitch(seq_no=1, engaged=True)
    assert (kill.account_id, kill.cancel_open) == (None, False)


def test_parse_line_and_text_formats() -> None:
    assert mf.parse_line("# comment") is None
    assert mf.parse_line("T 1500") == mf.ClockSet(timestamp=1500)
    assert mf.parse_line("C 4 9") == mf.Cancel(seq_no=4, order_id=9)
    with pytest.raises(mf.ParseError):
        mf.parse_line("X 1")
    e = mf.Engine()
    e.process(mf.parse_line("N 1 1 1 B LMT GTC 0 CN 100 5"))
    assert mf.format_top_of_book(e, 1) == "BOOK seq=1 bid=100x5 ask=-"
    assert mf.format_depth(e) == "LVL side=B px=100 qty=5 orders=1:5\n"


def test_check_invariants_passes_on_a_healthy_engine(flow: Flow) -> None:
    flow.limit(1, 1, B, 100, 5)
    flow.engine.check_invariants()
