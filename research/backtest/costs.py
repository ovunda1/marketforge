"""Transaction costs (M9): fee, spread, slippage, impact and financing models.

Both backtest modes hand their trades to one ``CostModel``, made of the components the
config turns on (``build_cost_model``). Every component returns whole money units per
trade, rounded up: a cost is never rounded down, and a rebate (a negative cost) is
rounded toward zero. Money units are the dataset's price unit (micro-dollars for bars,
the instrument's tick for event data), so a fee given in dollars converts exactly.

**What each mode may charge.** In event mode every fill comes out of the engine's book,
so the fill price already contains the spread, the queue and the latency; charging a
spread, slippage, impact or latency model on top would count them twice, and building
one is an error. Event mode charges fees only (commission, maker-taker by the fill's
liquidity flag, the SEC fee) and *measures* the rest as implementation shortfall. Bar
fills are at the open or close print, which has no spread in it, so bar mode charges a
half-spread explicitly and treats every fill as taking liquidity.

**Point-in-time inputs.** Impact, volatility-scaled and latency slippage need a daily
volatility and an average daily dollar volume. For a fill on bar ``e`` they come from
bars ``e - window .. e - 1`` only (``CostInputs``): an open fill cannot know its own
bar's volume. Dollar volume is ``volume * close`` of the same unadjusted bar, so a split
does not move it; volatility uses split- and dividend-adjusted returns. With fewer than
``min_periods`` returns, those components charge ``fallback_bps`` instead, and the trade
is counted.

**Financing** (bar mode) is not per trade: ``Financing`` is charged by the backtester
on each bar's balances (``research.backtest.vectorized``).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

SCHEDULES = Path(__file__).resolve().parent / "schedules"
BAR, EVENT = "bar", "event"


@dataclass(frozen=True)
class TradeRecord:
    """One execution. ``qty`` is signed (+ buy, - sell), ``price`` in the dataset's price
    unit. Bar mode sets ``bar`` (the bar it filled on), ``volume`` (that bar's volume,
    not known to an open fill: costs never read it), ``signal_bar`` and
    ``decision_price`` (the signal bar's close, split-adjusted to the fill's shares) and
    ``kind`` (``rebalance`` or ``liquidation`` at a window's end). Event mode sets ``ts``
    (ns) and ``liquidity`` (``MAKER``/``TAKER``)."""

    symbol: int
    qty: int
    price: int
    bar: int | None = None
    volume: int | None = None
    ts: int | None = None
    liquidity: str | None = None
    signal_bar: int | None = None
    decision_price: int | None = None
    kind: str = "rebalance"

    @property
    def notional(self) -> int:
        return abs(self.qty) * self.price


@dataclass(frozen=True)
class CostInputs:
    """Daily volatility, dollar ADV (money units) and the number of returns behind them,
    per [bar, symbol], from the bars before each bar."""

    vol: np.ndarray
    adv: np.ndarray
    n_obs: np.ndarray

    @classmethod
    def from_panel(cls, panel: Any, window: int = 20, min_periods: int = 5) -> CostInputs:
        """Rolling sums over bars ``e - window .. e - 1`` for each bar ``e``: bar ``e``
        itself never enters its own inputs."""
        if window < 2 or min_periods < 2:
            raise ValueError("[costs.inputs]: window and min_periods must be >= 2 (a volatility needs two returns)")
        T, N = panel.shape
        tr = panel.total_return_index()
        ret = np.full((T, N), np.nan)
        ret[1:] = tr[1:] / tr[:-1] - 1
        dollar = np.where(panel.valid, panel.volume.astype(float) * panel.close.astype(float), np.nan)

        def window_sums(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            ok = np.isfinite(x)
            v = np.where(ok, x, 0.0)
            # c[e] = sum over rows < e; the window of e is rows max(0, e - window) .. e - 1.
            c = np.zeros((T + 1, N))
            c[1:] = np.cumsum(ok, axis=0)
            s1 = np.zeros((T + 1, N))
            s1[1:] = np.cumsum(v, axis=0)
            s2 = np.zeros((T + 1, N))
            s2[1:] = np.cumsum(v * v, axis=0)
            hi = np.arange(T)
            lo = np.maximum(0, hi - window)
            return c[hi] - c[lo], s1[hi] - s1[lo], s2[hi] - s2[lo]

        n, s1, s2 = window_sums(ret)
        var = np.where(n > 1, (s2 - s1 * s1 / np.maximum(n, 1)) / np.maximum(n - 1, 1), np.nan)
        vol = np.sqrt(np.maximum(var, 0.0))
        m, d1, _ = window_sums(dollar)
        adv = np.where(m > 0, d1 / np.maximum(m, 1), np.nan)
        return cls(vol=vol, adv=adv, n_obs=n.astype(np.int64))


@dataclass(frozen=True)
class CostContext:
    mode: str
    money_unit: Fraction                  # dollars per money unit
    dates: np.ndarray | None = None       # datetime64[D] per bar (bar mode)
    inputs: CostInputs | None = None
    min_periods: int = 5
    fallback_bps: float = 50.0

    def date(self, t: TradeRecord) -> np.datetime64:
        if t.ts is not None:
            return np.datetime64(int(t.ts), "ns").astype("datetime64[D]")
        assert self.dates is not None and t.bar is not None
        return self.dates[t.bar]

    def market(self, t: TradeRecord) -> tuple[float, float] | None:
        """(daily vol, dollar ADV) known before the trade's bar, or None if too little history."""
        if self.inputs is None or t.bar is None:
            return None
        j = t.symbol
        n = int(self.inputs.n_obs[t.bar, j])
        vol, adv = float(self.inputs.vol[t.bar, j]), float(self.inputs.adv[t.bar, j])
        if n < self.min_periods or not (np.isfinite(vol) and np.isfinite(adv)) or adv <= 0:
            return None
        return vol, adv

    def dollars(self, text: str) -> Fraction:
        """A dollar amount (decimal text) in money units, exactly."""
        return Fraction(Decimal(text)) / self.money_unit


def _ceil(x: Fraction | float) -> int:
    """Round a cost up. Float estimates (impact, slippage) are first rounded to 12
    significant digits, so a product like 0.02 * 0.1 * 1e12 = 2000000000.0000005 is not
    charged a unit more, and the result does not depend on a platform's last float bit."""
    if isinstance(x, float):
        x = float(f"{x:.12g}")
    return math.ceil(x)


def _bps(bps: float) -> Fraction:
    return Fraction(Decimal(str(bps))) / 10_000


class Component:
    name: ClassVar[str]
    modes: ClassVar[frozenset[str]] = frozenset({BAR, EVENT})
    needs_market: ClassVar[bool] = False
    keys: ClassVar[dict[str, Any]] = {}

    def __init__(self, **params: Any) -> None:
        unknown = set(params) - set(self.keys)
        if unknown:
            raise ValueError(f"[costs.{self.name}]: unknown key(s) {', '.join(sorted(unknown))} "
                             f"(allowed: {', '.join(sorted(self.keys))})")
        self.params = {k: params.get(k, default) for k, default in self.keys.items()}
        self.validate()

    def validate(self) -> None:
        pass

    def describe(self) -> dict[str, Any]:
        return dict(self.params)

    def cost(self, t: TradeRecord, ctx: CostContext, market: tuple[float, float] | None) -> int:
        raise NotImplementedError


class Commission(Component):
    """Per share (dollars, decimal text) plus bps of notional, on every fill."""

    name = "commission"
    keys = {"per_share": "0", "bps": 0.0}

    def validate(self) -> None:
        self.params["per_share"] = str(self.params["per_share"])
        self.params["bps"] = float(self.params["bps"])
        if Decimal(self.params["per_share"]) < 0 or self.params["bps"] < 0:
            raise ValueError("[costs.commission]: per_share and bps must be >= 0 (rebates are maker_taker's)")

    def cost(self, t, ctx, market):
        return _ceil(abs(t.qty) * ctx.dollars(self.params["per_share"]) + t.notional * _bps(self.params["bps"]))


class MakerTaker(Component):
    """Exchange fees by liquidity flag; a negative fee is a rebate. Bar fills take."""

    name = "maker_taker"
    keys = {"maker_per_share": "0", "taker_per_share": "0", "maker_bps": 0.0, "taker_bps": 0.0}

    def validate(self) -> None:
        for k in ("maker_per_share", "taker_per_share"):
            self.params[k] = str(self.params[k])
            Decimal(self.params[k])
        for k in ("maker_bps", "taker_bps"):
            self.params[k] = float(self.params[k])

    def cost(self, t, ctx, market):
        side = "maker" if t.liquidity == "MAKER" else "taker"
        return _ceil(abs(t.qty) * ctx.dollars(self.params[f"{side}_per_share"])
                     + t.notional * _bps(self.params[f"{side}_bps"]))


def load_schedule(name: str) -> list[dict[str, str]]:
    path = SCHEDULES / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"unknown fee schedule {name!r} (known: {sorted(p.stem for p in SCHEDULES.glob('*.json'))})")
    return json.loads(path.read_text())["rows"]


class SecFee(Component):
    """SEC Section 31 fee on sales: dollars per million of proceeds, at the rate in force on
    the trade date. ``schedule`` is a built-in name (``sec31``) or a list of
    ``{"from": date, "rate_per_million": text}`` rows."""

    name = "sec_fee"
    keys = {"schedule": "sec31"}

    def validate(self) -> None:
        rows = self.params["schedule"]
        if isinstance(rows, str):
            rows = load_schedule(rows)
        if not isinstance(rows, list) or not rows:
            raise ValueError("[costs.sec_fee]: schedule must be a name or a non-empty list of rows")
        for r in rows:
            if not {"from", "rate_per_million"} <= set(r):
                raise ValueError("[costs.sec_fee]: each row needs from and rate_per_million")
        if [r["from"] for r in rows] != sorted(r["from"] for r in rows):
            raise ValueError("[costs.sec_fee]: schedule rows must be in date order")
        self.params["schedule"] = rows
        self._from = np.array([r["from"] for r in rows], dtype="datetime64[D]")
        self._rate = [Fraction(Decimal(r["rate_per_million"])) / 1_000_000 for r in rows]

    def cost(self, t, ctx, market):
        if t.qty >= 0:
            return 0
        day = ctx.date(t)
        i = int(np.searchsorted(self._from, day, side="right")) - 1
        if i < 0:
            raise ValueError(f"no SEC fee rate for {day}: the schedule starts {self._from[0]}")
        return _ceil(t.notional * self._rate[i])


class Spread(Component):
    """Half the quoted spread, in bps of notional, on each fill (bar mode)."""

    name = "spread"
    modes = frozenset({BAR})
    keys = {"half_spread_bps": 0.0}

    def cost(self, t, ctx, market):
        return _ceil(t.notional * _bps(self.params["half_spread_bps"]))


class FixedSlippage(Component):
    name = "slippage"
    modes = frozenset({BAR})
    keys = {"bps": 0.0}

    def cost(self, t, ctx, market):
        return _ceil(t.notional * _bps(self.params["bps"]))


class VolSlippage(Component):
    """``k`` daily standard deviations of adverse move: ``k * vol * notional``."""

    name = "vol_slippage"
    modes = frozenset({BAR})
    needs_market = True
    keys = {"k": 0.0}

    def cost(self, t, ctx, market):
        return _ceil(self.params["k"] * market[0] * t.notional)


class SqrtImpact(Component):
    """Square-root impact: ``y * vol * sqrt(notional / ADV) * notional``."""

    name = "impact"
    modes = frozenset({BAR})
    needs_market = True
    keys = {"y": 1.0}

    def cost(self, t, ctx, market):
        vol, adv = market
        return _ceil(self.params["y"] * vol * math.sqrt(t.notional / adv) * t.notional)


class LatencySlippage(Component):
    """The adverse move while an order is in flight, charged as ``k`` standard deviations
    of a random walk over the latency: ``k * vol * sqrt(latency / session) * notional``."""

    name = "latency"
    modes = frozenset({BAR})
    needs_market = True
    keys = {"latency_ms": 0.0, "k": 1.0, "session_seconds": 23_400}

    def cost(self, t, ctx, market):
        frac = self.params["latency_ms"] / 1000 / self.params["session_seconds"]
        return _ceil(self.params["k"] * market[0] * math.sqrt(frac) * t.notional)


COMPONENTS: dict[str, type[Component]] = {c.name: c for c in (Commission, MakerTaker, SecFee, Spread, FixedSlippage,
                                                              VolSlippage, SqrtImpact, LatencySlippage)}


@dataclass(frozen=True)
class Financing:
    """Bar mode: shorts pay ``borrow_bps`` a year on their value at the previous close, a
    debit cash balance pays ``spread_bps`` a year over the risk-free rate; actual/360."""

    borrow_bps: float = 0.0
    spread_bps: float = 0.0

    def describe(self) -> dict[str, Any]:
        return {"borrow_bps": float(self.borrow_bps), "spread_bps": float(self.spread_bps), "day_count": "actual/360"}

    def daily(self, bps: float) -> Fraction:
        return _bps(bps) / 360


@dataclass(frozen=True)
class InputParams:
    window: int = 20
    min_periods: int = 5
    fallback_bps: float = 50.0

    def describe(self) -> dict[str, Any]:
        return {"window": self.window, "min_periods": self.min_periods, "fallback_bps": float(self.fallback_bps),
                "known_through": "bar before the fill"}


@dataclass
class Charges:
    components: dict[str, np.ndarray]   # per trade, int64
    total: np.ndarray
    fallback: np.ndarray                # bool: a market-input component used the fallback
    participation: np.ndarray           # notional / ADV (nan when unknown)


@dataclass
class CostModel:
    components: Sequence[Component] = ()
    financing: Financing | None = None
    inputs: InputParams = field(default_factory=InputParams)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.components)

    @property
    def needs_market(self) -> bool:
        return any(c.needs_market for c in self.components)

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {c.name: c.describe() for c in self.components}
        if self.financing is not None:
            out["financing"] = self.financing.describe()
        if self.needs_market:
            out["inputs"] = self.inputs.describe()
        return out

    def bar_context(self, panel: Any, money_unit: str | Fraction) -> CostContext:
        inputs = CostInputs.from_panel(panel, self.inputs.window, self.inputs.min_periods)
        return CostContext(mode=BAR, money_unit=Fraction(money_unit), dates=panel.dates.astype("datetime64[D]"),
                           inputs=inputs, min_periods=self.inputs.min_periods, fallback_bps=self.inputs.fallback_bps)

    def charge(self, trades: Sequence[TradeRecord], ctx: CostContext) -> Charges:
        n = len(trades)
        comps = {c.name: np.zeros(n, np.int64) for c in self.components}
        fallback = np.zeros(n, bool)
        participation = np.full(n, np.nan)
        for i, t in enumerate(trades):
            market = ctx.market(t)
            if market is not None:
                participation[i] = t.notional / market[1]
            for c in self.components:
                if c.needs_market and market is None:
                    fallback[i] = True
                    comps[c.name][i] = _ceil(t.notional * _bps(ctx.fallback_bps))
                else:
                    comps[c.name][i] = c.cost(t, ctx, market)
        total = sum(comps.values(), np.zeros(n, np.int64))
        return Charges(components=comps, total=total, fallback=fallback, participation=participation)

    def costs(self, trades: Sequence[TradeRecord], ctx: CostContext) -> list[int]:
        return self.charge(trades, ctx).total.tolist()


def ZeroCost() -> CostModel:  # noqa: N802 - kept from M8: a model that charges nothing
    return CostModel()


def build_cost_model(section: dict[str, Any], mode: str) -> CostModel:
    """The model a ``[costs]`` table describes: each sub-table turns a component on
    (``enabled = false`` keeps it written down but off); ``inputs`` and ``financing``
    are special. Unknown names and keys are errors."""
    if mode not in (BAR, EVENT):
        raise ValueError(f"mode must be {BAR!r} or {EVENT!r}")
    components: list[Component] = []
    financing = None
    inputs = InputParams()
    for name, params in section.items():
        if not isinstance(params, dict):
            raise ValueError(f"[costs.{name}] must be a table")
        params = dict(params)
        if params.pop("enabled", True) is False:
            continue
        if name == "inputs":
            unknown = set(params) - {"window", "min_periods", "fallback_bps"}
            if unknown:
                raise ValueError(f"[costs.inputs]: unknown key(s) {', '.join(sorted(unknown))}")
            inputs = InputParams(**params)
            continue
        if name == "financing":
            if mode == EVENT:
                raise ValueError("[costs.financing] is bar-mode only: an event run is one session, and its fill "
                                 "price already contains everything that happened in it")
            unknown = set(params) - {"borrow_bps", "spread_bps"}
            if unknown:
                raise ValueError(f"[costs.financing]: unknown key(s) {', '.join(sorted(unknown))}")
            financing = Financing(**params)
            continue
        if name not in COMPONENTS:
            raise ValueError(f"unknown cost component [costs.{name}] (known: {', '.join(sorted(COMPONENTS))}, "
                             "inputs, financing)")
        cls = COMPONENTS[name]
        if mode not in cls.modes:
            raise ValueError(f"[costs.{name}] cannot be used in event mode: the fill price from the engine's book "
                             "already contains the spread, slippage, impact and latency (it is measured as "
                             "implementation shortfall instead)")
        components.append(cls(**params))
    return CostModel(components=tuple(components), financing=financing, inputs=inputs)
