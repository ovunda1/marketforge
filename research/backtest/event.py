"""Event-driven backtests through the real engine (M8).

``run_event_backtest`` replays recorded quotes and trades (``research.backtest.historical``)
with the strategy's orders entering through the broker. ``run_live_backtest`` puts the
strategy into the synthetic agents' market instead (``research.sim``), where the agents
react to it. Both use one ``mfengine.Engine``: the same matching code as the live
exchange. Use the release install (``make venv-release``) for real runs; the dev install
checks the book's invariants after every command and is several times slower.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mfengine
import pandas as pd

from research.backtest.broker import Broker
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
        "cost_model": "zero",
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


def _run(sim: Simulation, broker: Broker, config: EventConfig, journal_path: str | Path | None,
         flow: str) -> EventResult:
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
    return EventResult(
        fills=broker.fills, updates=broker.updates, position=broker.position, cash=broker.cash,
        pnl=None if mark is None else broker.cash + broker.position * mark, mark=mark,
        engine_position=sim.engine.position(config.strategy_account), commands=sim.commands,
        events_sha256=mfengine.replay(journal_path).sha256 if journal_path is not None else None,
        assumptions=_assumptions(config, flow), engine=engine_info(),
    )


def run_event_backtest(quotes: pd.DataFrame, trades: pd.DataFrame, strategy: Strategy,
                       config: EventConfig = EventConfig(), *, journal_path: str | Path | None = None,
                       engine: mfengine.Engine | None = None) -> EventResult:
    sim = Simulation(engine, journal_path)
    flow = HistoricalFlow(sim, quotes, trades, queue=config.queue, consumed_liquidity=config.consumed_liquidity)
    broker = Broker(sim, strategy, account=config.strategy_account, latency_ns=config.latency_ns,
                    jitter_ns=config.jitter_ns, seed=config.seed, session_start=flow.start, session_end=flow.end)
    flow.schedule()
    return _run(sim, broker, config, journal_path, "historical_replay")


def run_live_backtest(synth: Any, strategy: Strategy, config: EventConfig = EventConfig(), *,
                      journal_path: str | Path | None = None) -> EventResult:
    """The strategy trades among the synthetic agents (``synth`` is a ``SynthConfig``),
    which see and react to its orders."""
    from research.sim.market import SyntheticMarket

    sim = Simulation(None, journal_path)
    market = SyntheticMarket(sim, synth)
    broker = Broker(sim, strategy, account=config.strategy_account, latency_ns=config.latency_ns,
                    jitter_ns=config.jitter_ns, seed=config.seed, session_start=0, session_end=synth.duration_ns)
    market.schedule()
    result = _run(sim, broker, config, journal_path, "live_agents")
    result.assumptions.update(queue="engine", consumed_liquidity="engine", tie_break="agents_first",
                              same_timestamp="n/a")
    return result
