"""Event-driven backtests through the real engine (M8).

``run_event_backtest`` replays recorded quotes and trades (``research.backtest.historical``)
with the strategy's orders entering through the broker. ``run_live_backtest`` puts the
strategy into the synthetic agents' market instead (``research.sim``), where the agents
react to it. Both use one ``mfengine.Engine``: the same matching code as the live
exchange. Use the release install (``make venv-release``) for real runs; the dev install
checks the book's invariants after every command and is several times slower.

**Costs (M9).** The fills come out of the book, so ``pnl`` (gross) already pays the
spread, the queue and the latency. A cost model here charges fees only
(``research.backtest.costs`` refuses the rest in event mode); ``net_pnl`` subtracts them.
The rest is *measured* as implementation shortfall, inside gross: ``delay`` is the mid
moving between the strategy's decision and the order reaching the engine, ``spread_paid``
is each fill against the mid on arrival (signed: a buy above it pays).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import mfengine
import pandas as pd

from research.backtest.broker import Broker
from research.backtest.costs import EVENT, CostContext, CostModel, TradeRecord
from research.backtest.historical import CONSUMED_MODELS, QUEUE_MODELS, HistoricalFlow
from research.backtest.loop import Simulation
from research.backtest.strategy import OrderUpdate, Strategy, StrategyFill


class BacktestError(RuntimeError):
    pass


@dataclass(frozen=True)
class EventConfig:
    latency_ns: int = 0
    jitter_ns: int = 0
    seed: int = 0
    queue: str = "pessimistic"
    consumed_liquidity: str = "stays_removed"
    strategy_account: int = 100
    risk: mfengine.AccountLimits | None = None

    def __post_init__(self) -> None:
        if self.queue not in QUEUE_MODELS:
            raise ValueError(f"queue must be one of {QUEUE_MODELS}, not {self.queue!r}")
        if self.consumed_liquidity not in CONSUMED_MODELS:
            raise ValueError(f"consumed_liquidity must be one of {CONSUMED_MODELS}, not {self.consumed_liquidity!r}")
        if self.latency_ns < 0 or self.jitter_ns < 0:
            raise ValueError("latency_ns and jitter_ns must be >= 0")
        if self.strategy_account < 100:
            raise ValueError("strategy accounts start at 100 (lower ids are the replay's and the agents')")


@dataclass
class EventResult:
    fills: list[StrategyFill]
    updates: list[OrderUpdate]
    position: int
    cash: int
    pnl: int | None
    mark: int | None
    engine_position: int
    commands: int
    events_sha256: str | None
    assumptions: dict[str, Any]
    engine: dict[str, Any] = field(default_factory=dict)
    costs: dict[str, int] = field(default_factory=dict)       # fees by component (M9)
    net_pnl: int | None = None
    shortfall: dict[str, Any] = field(default_factory=dict)
    trade_costs: dict[str, list[int]] = field(default_factory=dict)


def engine_info() -> dict[str, Any]:
    return {"version": mfengine.version(), "build_type": mfengine.BUILD_TYPE,
            "invariant_checks": mfengine.INVARIANT_CHECKS}


def _assumptions(config: EventConfig, flow: str) -> dict[str, Any]:
    return {
        "flow": flow,
        "queue": config.queue,
        "consumed_liquidity": config.consumed_liquidity,
        "latency_ns": config.latency_ns,
        "jitter_ns": config.jitter_ns,
        "market_data_latency_ns": 0,
        "tie_break": "historical_first",
        "same_timestamp": "trades_before_quotes",
        "depth": "L1",
        "quotes_take_strategy_liquidity": False,
        "mark": "exit_side",
    }


def _mark(broker: Broker) -> int | None:
    """Mark the position where it could be closed: a long at the last bid, a short at
    the last ask."""
    if broker.position == 0:
        return 0
    q = broker.view.quotes()
    prices = q["bid"] if broker.position > 0 else q["ask"]
    prices = prices[prices > 0]
    return int(prices[-1]) if len(prices) else None


def _shortfall(broker: Broker) -> dict[str, Any]:
    delay2, spread2, missing = 0, 0, 0
    for f in broker.fills:
        signed = f.qty if f.side == mfengine.Side.BUY else -f.qty
        decision, arrival = broker.marks.get(f.client_id, [None, None])
        if decision is None or arrival is None:
            missing += 1
            continue
        delay2 += signed * (arrival - decision)
        spread2 += signed * (2 * f.price - arrival)
    half = Fraction(1, 2)
    return {"delay": delay2 * half, "spread_paid": spread2 * half, "fills_without_quotes": missing}


def _charge(broker: Broker, model: CostModel | None, tick_size: str) -> tuple[dict[str, int], dict[str, list[int]]]:
    if model is None or not model.components:
        return {}, {}
    trades = [TradeRecord(symbol=0, qty=f.qty if f.side == mfengine.Side.BUY else -f.qty, price=f.price, ts=f.ts,
                          liquidity=f.liquidity.name) for f in broker.fills]
    ch = model.charge(trades, CostContext(mode=EVENT, money_unit=Fraction(tick_size)))
    return {k: int(v.sum()) for k, v in ch.components.items()}, {k: v.tolist() for k, v in ch.components.items()}


def _run(sim: Simulation, broker: Broker, config: EventConfig, journal_path: str | Path | None,
         flow: str, cost_model: CostModel | None = None, tick_size: str = "1") -> EventResult:
    try:
        if config.risk is not None:
            sim.now = broker.session_start
            sim.process(mfengine.SetAccountLimits(seq_no=0, limits=config.risk, account_id=config.strategy_account))
        broker.start()
        sim.run()
        broker.end()
    except mfengine.MarketForgeError as err:
        raise BacktestError(f"engine error at t={sim.now}: {err}") from err
    finally:
        sim.close()
    mark = _mark(broker)
    costs, trade_costs = _charge(broker, cost_model, tick_size)
    pnl = None if mark is None else broker.cash + broker.position * mark
    return EventResult(
        fills=broker.fills, updates=broker.updates, position=broker.position, cash=broker.cash,
        pnl=None if mark is None else broker.cash + broker.position * mark, mark=mark,
        engine_position=sim.engine.position(config.strategy_account), commands=sim.commands,
        events_sha256=mfengine.replay(journal_path).sha256 if journal_path is not None else None,
        assumptions=_assumptions(config, flow), engine=engine_info(),
        costs=costs, net_pnl=None if pnl is None else pnl - sum(costs.values()), shortfall=_shortfall(broker),
        trade_costs=trade_costs,
    )


def run_event_backtest(quotes: pd.DataFrame, trades: pd.DataFrame, strategy: Strategy,
                       config: EventConfig = EventConfig(), *, journal_path: str | Path | None = None,
                       engine: mfengine.Engine | None = None, cost_model: CostModel | None = None,
                       tick_size: str = "1") -> EventResult:
    """``tick_size`` is the dollars per price unit, to convert a cost model's dollar fees."""
    sim = Simulation(engine, journal_path)
    flow = HistoricalFlow(sim, quotes, trades, queue=config.queue, consumed_liquidity=config.consumed_liquidity)
    broker = Broker(sim, strategy, account=config.strategy_account, latency_ns=config.latency_ns,
                    jitter_ns=config.jitter_ns, seed=config.seed, session_start=flow.start, session_end=flow.end)
    flow.schedule()
    return _run(sim, broker, config, journal_path, "historical_replay", cost_model, tick_size)


def run_live_backtest(synth: Any, strategy: Strategy, config: EventConfig = EventConfig(), *,
                      journal_path: str | Path | None = None, cost_model: CostModel | None = None) -> EventResult:
    """The strategy trades among the synthetic agents (``synth`` is a ``SynthConfig``),
    which see and react to its orders."""
    from research.sim.market import SyntheticMarket

    sim = Simulation(None, journal_path)
    market = SyntheticMarket(sim, synth)
    broker = Broker(sim, strategy, account=config.strategy_account, latency_ns=config.latency_ns,
                    jitter_ns=config.jitter_ns, seed=config.seed, session_start=0, session_end=synth.duration_ns)
    market.schedule()
    result = _run(sim, broker, config, journal_path, "live_agents", cost_model, synth.tick_size)
    result.assumptions.update(queue="engine", consumed_liquidity="engine", tie_break="agents_first",
                              same_timestamp="n/a")
    return result
