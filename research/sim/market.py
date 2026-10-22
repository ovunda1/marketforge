"""The synthetic market (M8): agents trading through one engine, from one seed.

``generate`` runs the agents alone and records the public tape (trades and top-of-book
quotes) as normalized datasets, so synthetic data goes through the same replay path as
recorded data. ``SyntheticMarket`` is also used live, with a strategy among the agents
(``research.backtest.event.run_live_backtest``).
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import mfengine
import numpy as np
import pandas as pd

from research.backtest.loop import MarketData, Priority, Simulation
from research.data import schema, store
from research.sim.agents import (SEC, FairValue, FairValueConfig, MarketMaker, MarketMakerConfig,
                                 MomentumConfig, MomentumTrader, NoiseConfig, NoiseTraders)

__all__ = ["SEC", "SynthConfig", "SynthResult", "SyntheticMarket", "generate"]

MARKET_MAKER_ACCOUNT = 20
MOMENTUM_BASE_ACCOUNT = 30


@dataclass(frozen=True)
class SynthConfig:
    seed: int
    duration_ns: int = 60 * SEC
    start_price: int = 10_000
    tick_size: str = "0.01"
    lot_size: str = "1"
    fair: FairValueConfig = field(default_factory=FairValueConfig)
    noise: NoiseConfig = field(default_factory=NoiseConfig)
    market_maker: MarketMakerConfig = field(default_factory=MarketMakerConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)

    NESTED = {"fair": FairValueConfig, "noise": NoiseConfig, "market_maker": MarketMakerConfig,
              "momentum": MomentumConfig}

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynthConfig:
        """Strict: an unknown key is an error, missing keys take their defaults."""
        names = {f.name for f in dataclasses.fields(cls)}
        unknown = set(data) - names
        if unknown:
            raise ValueError(f"unknown synthetic market setting(s): {', '.join(sorted(unknown))}")
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            nested = cls.NESTED.get(key)
            if nested is not None:
                sub = {f.name for f in dataclasses.fields(nested)}
                bad = set(value) - sub
                if bad:
                    raise ValueError(f"unknown {key} setting(s): {', '.join(sorted(bad))}")
                kwargs[key] = nested(**value)
            else:
                kwargs[key] = value
        return cls(**kwargs)


class SyntheticMarket:
    """The agents, wired into ``sim``. Each gets its own random stream, spawned from the
    seed in a fixed order."""

    def __init__(self, sim: Simulation, config: SynthConfig) -> None:
        self.sim, self.config = sim, config
        streams = np.random.SeedSequence(config.seed).spawn(2 + config.momentum.count)
        rngs = [np.random.default_rng(s) for s in streams]
        self.fair = FairValue(sim, config.fair, config.start_price, rngs[0], end=config.duration_ns)
        self.noise = NoiseTraders(sim, config.noise, self.fair, rngs[1])
        self.market_maker = MarketMaker(config.market_maker, MARKET_MAKER_ACCOUNT, sim, self.fair)
        self.momentum = [MomentumTrader(config.momentum, MOMENTUM_BASE_ACCOUNT + i, sim)
                         for i in range(config.momentum.count)]
        for agent in (self.noise, self.market_maker, *self.momentum):
            agent.end = config.duration_ns
        for m in self.momentum:
            sim.subscribe(m)

    def schedule(self) -> None:
        """Start everything at time 0. Agents stop waking up at the duration."""
        self.fair.schedule()
        self.noise.schedule()
        self.sim.at(0, Priority.AGENT, self.market_maker.schedule)

    def fills_by_agent(self) -> dict[str, int]:
        return {"noise": self.noise.fills, "market_maker": self.market_maker.fills,
                "momentum": sum(m.fills for m in self.momentum)}


class _Tape:
    """Records the public tape: every trade, and the top of book whenever it changes."""

    def __init__(self) -> None:
        self.quotes: list[tuple[int, int, int, int, int]] = []
        self.trades: list[tuple[int, int, int, int, int]] = []

    def on_market_data(self, sim: Simulation, md: MarketData) -> None:
        for t in md.trades:
            side = schema.BUY if t.aggressor == mfengine.Side.BUY else schema.SELL
            self.trades.append((md.ts, t.trade_id, t.price, t.qty, side))
        row = (md.ts, md.bid or 0, md.bid_qty, md.ask or 0, md.ask_qty)
        if not self.quotes or self.quotes[-1][1:] != row[1:]:
            self.quotes.append(row)


@dataclass
class SynthResult:
    quotes: pd.DataFrame
    trades: pd.DataFrame
    meta: dict[str, Any]
    digest: str
    reject_counts: dict[str, int]
    fills_by_agent: dict[str, int]
    arrivals: dict[str, int]
    commands: int


class _RejectCounter(Simulation):
    """A simulation that also counts every reject, whoever got it."""

    def __init__(self) -> None:
        super().__init__()
        self.rejects: Counter[str] = Counter()

    def process(self, command: Any) -> list[Any]:
        events = super().process(command)
        for e in events:
            if isinstance(e, mfengine.Reject):
                self.rejects[e.reason.name] += 1
        return events


def generate(config: SynthConfig) -> SynthResult:
    sim = _RejectCounter()
    market = SyntheticMarket(sim, config)
    tape = _Tape()
    sim.subscribe(tape)
    market.schedule()
    sim.run()
    quotes = pd.DataFrame(tape.quotes, columns=list(schema.COLUMNS["quotes"])).astype(schema.COLUMNS["quotes"])
    trades = pd.DataFrame(tape.trades, columns=list(schema.COLUMNS["trades"])).astype(schema.COLUMNS["trades"])
    meta = {"source": "synthetic", "generator": "research.sim.market", "seed": config.seed,
            "config": config.to_dict(),
            "instrument": {"symbol": "SYNTH", "tick_size": config.tick_size, "lot_size": config.lot_size}}
    digest = hashlib.sha256((store.content_hash(quotes, "quotes", meta) +
                             store.content_hash(trades, "trades", meta)).encode()).hexdigest()
    return SynthResult(quotes=quotes, trades=trades, meta=meta, digest=digest, reject_counts=dict(sim.rejects),
                       fills_by_agent=market.fills_by_agent(), arrivals={"noise": market.noise.arrivals},
                       commands=sim.commands)
