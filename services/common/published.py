"""What the gateway may publish about a backtest (M11). Tiingo's terms forbid
redistributing its prices, so the API serves derived, portfolio-level results only:
the same rule as `research.analytics.tearsheet.check_portfolio_level` (no key named
after a symbol, no long list except the portfolio series), re-implemented here because
the gateway never imports the research stack (it loads the engine). A test checks the
two agree. `public_metrics` is applied to every metrics payload the API returns.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from services.common.safe_json import stringify_big

SERIES = ("dates", "gross_cum_return", "net_cum_return", "net_drawdown")  # research.analytics.report.SERIES
LONG = 60  # research.analytics.tearsheet.LONG


class NotPublishable(ValueError):
    pass


def check_portfolio_level(metrics: dict[str, Any], symbols: Iterable[str]) -> None:
    names = set(symbols)
    allowed = {("series", s) for s in SERIES}

    def walk(v: Any, path: tuple[str, ...]) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if k in names:
                    raise NotPublishable(f"not portfolio-level: key {'.'.join((*path, k))} names a symbol")
                walk(x, (*path, k))
        elif isinstance(v, list):
            if len(v) > LONG:
                if path not in allowed:
                    raise NotPublishable(f"not portfolio-level: {'.'.join(path)} is a {len(v)}-long series")
                if any(isinstance(x, (list, dict)) for x in v):
                    raise NotPublishable(f"not portfolio-level: {'.'.join(path)} holds nested values")
            for i, x in enumerate(v):
                walk(x, (*path, str(i)))

    walk(metrics, ())


def public_metrics(metrics: dict[str, Any] | None, symbols: Iterable[str]) -> dict[str, Any] | None:
    """The metrics as the API returns them: checked portfolio-level, big integers as text."""
    if metrics is None:
        return None
    check_portfolio_level(metrics, symbols)
    return stringify_big(metrics)  # type: ignore[no-any-return]
