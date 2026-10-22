"""The transaction-cost interface (M8). M8 reports gross results only: ``ZeroCost``.

M9 adds fee, spread, slippage and impact models as ``CostModel`` subclasses. Both
backtest modes hand their trades to the same model and subtract what it returns, so
gross and net come from one code path and stay comparable across modes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TradeRecord:
    """One execution. ``qty`` is signed (+ buy, - sell), ``price`` in the dataset's price
    unit. Bar mode sets ``bar`` (the bar it filled on) and ``volume`` (that bar's volume);
    event mode sets ``ts`` (ns) and ``liquidity`` (``MAKER``/``TAKER``)."""

    symbol: int
    qty: int
    price: int
    bar: int | None = None
    volume: int | None = None
    ts: int | None = None
    liquidity: str | None = None

    @property
    def notional(self) -> int:
        return abs(self.qty) * self.price


class CostModel(ABC):
    name: str

    @abstractmethod
    def costs(self, trades: Sequence[TradeRecord]) -> list[int]:
        """The cost of each trade, in price units (>= 0 is a cost, < 0 a rebate)."""


class ZeroCost(CostModel):
    name = "zero"

    def costs(self, trades: Sequence[TradeRecord]) -> list[int]:
        return [0] * len(trades)


COST_MODELS: dict[str, type[CostModel]] = {"zero": ZeroCost}
