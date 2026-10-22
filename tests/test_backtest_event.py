"""Event-driven backtester (M8): historical quotes and trades replayed into the real
engine, strategy orders entering through a broker with latency. Conservative defaults:
pessimistic queue position, liquidity the strategy takes stays taken."""

from collections.abc import Callable

import mfengine
import pandas as pd
import pytest

from research.backtest.event import BacktestError, EventConfig, run_event_backtest, run_live_backtest
from research.backtest.historical import LIQUIDITY_ACCOUNT, TAPE_ACCOUNT
from research.backtest.strategy import Context, LookAheadError, MarketData, Strategy
from research.data import schema
from research.sim.market import SEC, SynthConfig, generate

BUY, SELL = mfengine.Side.BUY, mfengine.Side.SELL
IOC = mfengine.TimeInForce.IOC


def Q(*rows: tuple[int, int, int, int, int]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "bid_px", "bid_qty", "ask_px", "ask_qty"])
    return df.astype(schema.COLUMNS["quotes"])


def T(*rows: tuple[int, int, int, int]) -> pd.DataFrame:
    """(ts, price, qty, aggressor side)"""
    df = pd.DataFrame([(ts, i + 1, px, qty, schema.BUY if side == BUY else schema.SELL)
                       for i, (ts, px, qty, side) in enumerate(rows)],
                      columns=["ts", "trade_id", "price", "qty", "aggressor"])
    return df.astype(schema.COLUMNS["trades"])


NO_TRADES = T()


class Script(Strategy):
    """Runs each action once, on the first market data at or after its time, and
    records everything it is told."""

    def __init__(self, actions: dict[int, Callable[[Context], None]] | None = None) -> None:
        self.actions = dict(sorted((actions or {}).items()))
        self.md: list[MarketData] = []
        self.fills = []
        self.updates = []

    def on_market_data(self, ctx: Context, md: MarketData) -> None:
        self.md.append(md)
        for at in [at for at in self.actions if at <= md.ts]:
            self.actions.pop(at)(ctx)

    def on_fill(self, ctx: Context, fill) -> None:
        self.fills.append(fill)

    def on_order_update(self, ctx: Context, update) -> None:
        self.updates.append(update)

    def top(self, ts: int) -> tuple:
        md = [m for m in self.md if m.ts == ts][-1]
        return md.bid, md.bid_qty, md.ask, md.ask_qty


# --- Replay alone -------------------------------------------------------------------------

def test_replay_reproduces_every_quote_as_the_top_of_book() -> None:
    quotes = Q((10, 100, 5, 102, 7), (20, 100, 3, 102, 7), (30, 101, 4, 102, 9), (40, 100, 2, 103, 1),
               (50, 0, 0, 103, 1))
    s = Script()
    run_event_backtest(quotes, NO_TRADES, s)
    for ts, bid, bq, ask, aq in quotes.itertuples(index=False):
        assert s.top(ts) == ((bid or None), bq, ask, aq)


# --- Queue position --------------------------------------------------------------------------

def test_strategy_waits_behind_the_displayed_size() -> None:
    quotes = Q((10, 100, 100, 105, 10))
    trades = T((20, 100, 60, SELL), (30, 100, 50, SELL))
    s = Script({10: lambda ctx: ctx.buy(10, price=100)})
    result = run_event_backtest(quotes, trades, s)
    assert [(f.ts, f.price, f.qty) for f in result.fills] == [(30, 100, 10)]


def queue_scenario(queue: str) -> list:
    quotes = Q((10, 100, 100, 105, 10),
               (15, 100, 150, 105, 10),  # 50 more join behind the strategy
               (20, 100, 90, 105, 10))   # 60 cancelled: from behind, or from ahead?
    trades = T((30, 100, 50, SELL))
    s = Script({10: lambda ctx: ctx.buy(10, price=100)})
    return run_event_backtest(quotes, trades, s, EventConfig(queue=queue)).fills


def test_pessimistic_queue_is_the_default() -> None:
    # Cancels come from behind the strategy first: 90 still ahead, a 50 trade misses it.
    assert EventConfig().queue == "pessimistic"
    assert queue_scenario("pessimistic") == []


def test_optimistic_queue_is_opt_in() -> None:
    # Cancels come from the front: 40 ahead, so a 50 trade reaches the strategy.
    fills = queue_scenario("optimistic")
    assert [(f.ts, f.qty) for f in fills] == [(30, 10)]


# --- Liquidity the strategy takes ---------------------------------------------------------------

def consumed_scenario(mode: str | None) -> Script:
    quotes = Q((10, 99, 10, 101, 50),
               (20, 98, 10, 101, 50),  # ask unchanged in the data
               (30, 98, 10, 101, 45),  # 5 traded or cancelled in reality
               (40, 98, 10, 102, 60),  # the level moved away
               (50, 98, 10, 101, 50))  # and came back: a new level
    s = Script({10: lambda ctx: ctx.buy(20, price=101, tif=IOC)})
    config = EventConfig() if mode is None else EventConfig(consumed_liquidity=mode)
    result = run_event_backtest(quotes, NO_TRADES, s, config)
    assert [(f.price, f.qty) for f in result.fills] == [(101, 20)]
    return s


def test_taken_liquidity_stays_removed_by_default() -> None:
    s = consumed_scenario(None)
    assert [s.top(ts)[3] for ts in (20, 30, 40, 50)] == [30, 25, 60, 50]


def test_restoring_taken_liquidity_is_opt_in() -> None:
    s = consumed_scenario("restore")
    assert [s.top(ts)[3] for ts in (20, 30, 40, 50)] == [50, 45, 60, 50]


def test_taken_liquidity_returns_once_the_level_clears() -> None:
    quotes = Q((10, 99, 10, 101, 50), (20, 99, 10, 0, 0), (30, 99, 10, 101, 50))
    s = Script({10: lambda ctx: ctx.buy(20, price=101, tif=IOC)})
    run_event_backtest(quotes, NO_TRADES, s)
    assert s.top(20)[2] is None and s.top(30)[2:] == (101, 50)


# --- How historical flow meets strategy orders ----------------------------------------------------

def test_quote_updates_never_take_strategy_liquidity() -> None:
    # The historical bid moves up to the strategy's resting ask. In the data it never
    # traded there, so it does not trade here: the level is left out instead.
    quotes = Q((10, 100, 10, 102, 10), (20, 101, 10, 102, 10))
    trades = T((30, 101, 5, BUY))
    s = Script({10: lambda ctx: ctx.sell(5, price=101)})
    result = run_event_backtest(quotes, trades, s)
    assert s.top(20)[0] is None
    # Only a real trade, the tape's buy at 101, reaches the strategy.
    assert [(f.ts, f.side, f.price, f.qty) for f in result.fills] == [(30, SELL, 101, 5)]


def test_tape_trades_hit_a_better_priced_strategy_order_first() -> None:
    quotes = Q((10, 100, 50, 105, 10))
    trades = T((20, 100, 10, SELL))
    s = Script({10: lambda ctx: ctx.buy(5, price=101)})
    result = run_event_backtest(quotes, trades, s)
    assert [(f.price, f.qty, f.liquidity) for f in result.fills] == [(101, 5, mfengine.Liquidity.MAKER)]


def test_market_orders_only_see_the_top_level() -> None:
    quotes = Q((10, 99, 10, 101, 30), (20, 99, 10, 101, 30))
    s = Script({10: lambda ctx: ctx.buy(100)})
    result = run_event_backtest(quotes, NO_TRADES, s)
    assert sum(f.qty for f in result.fills) == 30
    assert [(u.kind, u.qty) for u in s.updates if u.kind == "cancelled"] == [("cancelled", 70)]
    # Marked where it could be sold: at the bid.
    assert result.position == 30 and result.pnl == 30 * (99 - 101)
    assert result.assumptions["mark"] == "exit_side"


# --- Latency, ordering and sequencing ----------------------------------------------------------------

def latency_scenario(latency_ns: int) -> list:
    quotes = Q((10, 99, 10, 101, 10), (20, 99, 10, 103, 10))
    s = Script({10: lambda ctx: ctx.buy(5, price=101, tif=IOC)})
    return run_event_backtest(quotes, NO_TRADES, s, EventConfig(latency_ns=latency_ns)).fills


def test_latency_lets_the_market_move_first() -> None:
    assert [(f.ts, f.price) for f in latency_scenario(5)] == [(15, 101)]
    # Arriving at 20, the same time as the quote that lifts the offer: data goes first.
    assert latency_scenario(10) == []


def test_trades_go_before_quotes_at_the_same_timestamp(tmp_path) -> None:
    quotes = Q((10, 99, 10, 101, 10), (20, 99, 10, 101, 6))
    trades = T((20, 101, 3, BUY))
    journal = tmp_path / "j.mfj"
    run_event_backtest(quotes, trades, Script(), journal_path=journal)
    at20 = [r.command for r in mfengine.JournalReader(journal).read_all() if r.timestamp == 20]
    assert isinstance(at20[0], mfengine.NewOrder) and at20[0].account_id == TAPE_ACCOUNT
    assert isinstance(at20[1], mfengine.Replace)  # 7 left after the trade, 6 in the data


def test_jitter_is_seeded_and_keeps_order(tmp_path) -> None:
    quotes = Q((10, 99, 10, 101, 10), (1_000, 99, 10, 101, 10))

    def burst(ctx: Context) -> None:
        for _ in range(50):
            ctx.buy(1, price=90)

    runs = iter(range(10))

    def stamps(seed: int) -> list[tuple[int, int]]:
        journal = tmp_path / f"j{next(runs)}.mfj"
        cfg = EventConfig(latency_ns=5, jitter_ns=100, seed=seed)
        run_event_backtest(quotes, NO_TRADES, Script({10: burst}), cfg, journal_path=journal)
        return [(r.timestamp, r.command.order_id) for r in mfengine.JournalReader(journal).read_all()
                if isinstance(r.command, mfengine.NewOrder) and r.command.account_id == cfg.strategy_account]

    a = stamps(1)
    assert len(a) == 50
    assert a == sorted(a)  # one session: no order overtakes another
    assert a[0][0] >= 15 and len({t for t, _ in a}) > 1
    assert stamps(1) == a and stamps(2) != a


@pytest.fixture(scope="module")
def synthetic_day():
    return generate(SynthConfig(seed=11, duration_ns=10 * SEC))


class Pinger(Strategy):
    """Trades now and then so the journal mixes all three kinds of flow."""

    def __init__(self) -> None:
        self.n = 0

    def on_market_data(self, ctx: Context, md: MarketData) -> None:
        self.n += 1
        if self.n % 200 == 0 and md.ask is not None:
            ctx.buy(1, price=md.ask, tif=IOC)
        elif self.n % 200 == 100 and md.bid is not None:
            ctx.sell(1, price=md.bid, tif=IOC)


def test_journal_is_sequenced_and_replays_to_the_same_hash(synthetic_day, tmp_path) -> None:
    journal = tmp_path / "j.mfj"
    result = run_event_backtest(synthetic_day.quotes, synthetic_day.trades, Pinger(),
                                EventConfig(latency_ns=1_000_000), journal_path=journal)
    records = mfengine.JournalReader(journal).read_all()
    assert [r.seq_no for r in records] == list(range(1, len(records) + 1))
    assert all(a.timestamp <= b.timestamp for a, b in zip(records, records[1:]))
    accounts = {r.command.account_id for r in records if isinstance(r.command, mfengine.NewOrder)}
    assert accounts == {LIQUIDITY_ACCOUNT, TAPE_ACCOUNT, EventConfig().strategy_account}
    assert result.events_sha256 == mfengine.replay(journal).sha256 == mfengine.replay(journal).sha256
    assert result.fills
    assert result.position == result.engine_position == sum(
        f.qty if f.side == BUY else -f.qty for f in result.fills)


def test_same_inputs_same_result(synthetic_day, tmp_path) -> None:
    runs = [run_event_backtest(synthetic_day.quotes, synthetic_day.trades, Pinger(),
                               EventConfig(latency_ns=1_000_000, jitter_ns=500_000, seed=4),
                               journal_path=tmp_path / f"{i}.mfj") for i in range(2)]
    assert runs[0].events_sha256 == runs[1].events_sha256
    assert runs[0].fills == runs[1].fills


# --- Strategy interface -----------------------------------------------------------------------------

class Recorder(Strategy):
    def __init__(self) -> None:
        self.log: list[tuple[str, int]] = []

    def on_start(self, ctx: Context) -> None:
        self.log.append(("start", ctx.now))
        ctx.set_timer(7)

    def on_market_data(self, ctx: Context, md: MarketData) -> None:
        self.log.append(("md", md.ts))

    def on_timer(self, ctx: Context, t: int) -> None:
        self.log.append(("timer", t))

    def on_end(self, ctx: Context) -> None:
        self.log.append(("end", ctx.now))


def test_callbacks_run_in_order() -> None:
    quotes = Q((10, 99, 1, 101, 1), (20, 99, 2, 101, 1), (40, 99, 3, 101, 1))
    r = Recorder()
    run_event_backtest(quotes, NO_TRADES, r)
    assert r.log[0] == ("start", 10) and r.log[-1] == ("end", 40)
    assert [t for k, t in r.log if k == "timer"] == [17, 24, 31, 38]
    assert [t for _, t in r.log] == sorted(t for _, t in r.log)


class Peeker(Strategy):
    """Tries to see the future in every way the interface offers."""

    def __init__(self) -> None:
        self.checked = 0

    def _check(self, ctx: Context) -> None:
        q = ctx.view.quotes()
        assert len(q["ts"]) == 0 or q["ts"].max() <= ctx.now
        t = ctx.view.trades()
        assert len(t["ts"]) == 0 or t["ts"].max() <= ctx.now
        with pytest.raises(LookAheadError):
            ctx.view.asof(ctx.now + 1)
        if len(q["ts"]):
            with pytest.raises(ValueError):
                q["ts"][0] = 0  # history is read-only
        assert not hasattr(ctx, "engine")
        self.checked += 1

    def on_start(self, ctx: Context) -> None:
        ctx.set_timer(3)

    def on_market_data(self, ctx: Context, md: MarketData) -> None:
        assert md.ts == ctx.now
        self._check(ctx)

    def on_timer(self, ctx: Context, t: int) -> None:
        assert t == ctx.now
        self._check(ctx)


def test_strategies_cannot_see_the_future() -> None:
    quotes = Q((10, 99, 1, 101, 1), (20, 99, 2, 101, 1), (40, 98, 3, 101, 1))
    trades = T((15, 101, 1, BUY), (30, 99, 2, SELL))
    p = Peeker()
    run_event_backtest(quotes, trades, p)
    assert p.checked >= 12


def test_risk_rejects_reach_the_strategy() -> None:
    quotes = Q((10, 99, 10, 101, 50), (20, 99, 10, 101, 50))
    s = Script({10: lambda ctx: ctx.buy(10, price=101)})
    result = run_event_backtest(quotes, NO_TRADES, s, EventConfig(risk=mfengine.AccountLimits(position_limit=5)))
    assert [(u.kind, u.reason) for u in s.updates] == [("reject", "POSITION_LIMIT")]
    assert result.fills == []


def test_an_engine_failure_aborts_the_backtest() -> None:
    engine = mfengine.Engine()
    mfengine._core._inject_fault_after_next_command(engine)
    with pytest.raises(BacktestError) as err:
        run_event_backtest(Q((10, 99, 1, 101, 1)), NO_TRADES, Script(), engine=engine)
    assert isinstance(err.value.__cause__, mfengine.MarketForgeError)


def test_config_is_checked() -> None:
    for bad in ({"queue": "fifo"}, {"consumed_liquidity": "never"}, {"latency_ns": -1}, {"jitter_ns": -1}):
        with pytest.raises(ValueError):
            EventConfig(**bad)


def test_assumptions_and_engine_are_reported() -> None:
    result = run_event_backtest(Q((10, 99, 1, 101, 1)), NO_TRADES, Script(),
                                EventConfig(queue="optimistic", latency_ns=7))
    a = result.assumptions
    assert a["queue"] == "optimistic" and a["consumed_liquidity"] == "stays_removed"
    assert a["latency_ns"] == 7 and a["tie_break"] == "historical_first"
    assert a["same_timestamp"] == "trades_before_quotes" and a["depth"] == "L1"
    assert a["quotes_take_strategy_liquidity"] is False
    assert result.engine["invariant_checks"] == mfengine.INVARIANT_CHECKS


# --- Live agents --------------------------------------------------------------------------------------

def test_live_mode_puts_the_strategy_among_reacting_agents() -> None:
    synth = SynthConfig(seed=5, duration_ns=5 * SEC)
    a = run_live_backtest(synth, Pinger(), EventConfig(latency_ns=1_000_000))
    b = run_live_backtest(synth, Pinger(), EventConfig(latency_ns=1_000_000))
    assert a.fills and a.fills == b.fills
    assert a.assumptions["flow"] == "live_agents"
