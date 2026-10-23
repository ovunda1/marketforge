"""The instruments the exchange lists (M10), from a YAML file shared by the matcher, the
gateway and the database seed:

```yaml
instruments:
  - symbol: AAPL
    tick_size: "0.01"          # text, never a float
    risk_config: risk.yaml     # optional, relative to this file (docs/risk.md)
```
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")  # also a directory name


@dataclass(frozen=True)
class Instrument:
    symbol: str
    tick_size: str
    risk_config: Path | None = None

    def __post_init__(self) -> None:
        if not SYMBOL.match(self.symbol):
            raise ValueError(f"bad symbol {self.symbol!r}")
        if Decimal(self.tick_size) <= 0:
            raise ValueError(f"{self.symbol}: tick_size must be positive")


def load_instruments(path: str | Path) -> list[Instrument]:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    out = []
    for item in data["instruments"]:
        unknown = set(item) - {"symbol", "tick_size", "risk_config"}
        if unknown:
            raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
        if not isinstance(item["tick_size"], str):
            raise ValueError(f"{path}: {item['symbol']}: tick_size must be a quoted string")
        risk = item.get("risk_config")
        out.append(Instrument(symbol=item["symbol"], tick_size=item["tick_size"],
                              risk_config=(path.parent / risk) if risk else None))
    if len({i.symbol for i in out}) != len(out):
        raise ValueError(f"{path}: duplicate symbols")
    return out
