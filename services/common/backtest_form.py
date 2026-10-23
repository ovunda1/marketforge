"""The dashboard's backtest form (M11): a small, validated request that the gateway turns
into a walk-forward config (the `make backtest` schema). The dashboard validates the
same rules as you type (`dashboard/src/lib/backtestForm.ts`); this module is the
authority, and both are run against `tests/fixtures/backtest_form/cases.json`.

The data catalog comes from the worker (`services/worker/catalog.py`): per dataset, its
symbols with their first and last dates and the trading calendar. Dates only, never a
price. Walk-forward trains on `train_years` x 252 trading days, then tests on the next
252, rolling: the chosen range must hold at least one training plus one test window,
and the longest lookback must fit inside the training window.
"""

from __future__ import annotations

import bisect
import itertools
import re
from dataclasses import dataclass
from typing import Any

DAYS_PER_YEAR = 252
TEST_DAYS = DAYS_PER_YEAR
TRAIN_YEARS = (1, 2, 3, 4, 5)
MAX_GRID = 12
CAPITAL = 1_000_000_000_000  # $1M in micro-dollars, as the showcase

# Allowed values per parameter: small sets keep every run short and every grid honest
# about how many variations it tried.
STRATEGIES: dict[str, dict[str, Any]] = {
    "tsmom": {"label": "Time-series momentum", "min_symbols": 1, "params": {
        "lookback": [20, 60, 120, 250], "vol_window": [20, 60],
        "target_vol": [0.05, 0.10, 0.15], "max_leverage": [1.0, 2.0]}},
    "xsmom": {"label": "Cross-sectional momentum", "min_symbols": 5, "params": {
        "lookback_months": [3, 6, 12], "skip_months": [0, 1], "quantile": [0.2, 0.3, 0.5]}},
}

# The showcase's cost models (research/showcase/tsmom.toml), fixed in git before any real run.
COSTS: dict[str, Any] = {
    "commission": {"per_share": "0.0035"},
    "sec_fee": {"schedule": "sec31"},
    "spread": {"half_spread_bps": 2.0},
    "impact": {"y": 1.0},
    "inputs": {"window": 20, "min_periods": 5, "fallback_bps": 50.0},
    "financing": {"borrow_bps": 50.0, "spread_bps": 50.0},
}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FIELDS = ("dataset", "strategy", "params", "start", "end", "symbols", "train_years", "costs")


@dataclass(frozen=True)
class FieldError:
    field: str  # a FIELDS name, "params.<name>", or "range"
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


class FormInvalid(ValueError):
    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("; ".join(f"{e.field}: {e.message}" for e in errors))
        self.errors = errors


def lookback_days(strategy: str, params: dict[str, list[Any]]) -> int:
    """Trading days of history the most demanding combination needs before its first signal."""
    if strategy == "tsmom":
        return max(params["lookback"]) + max(params["vol_window"])
    months = max(a + b for a, b in itertools.product(params["lookback_months"], params["skip_months"]))
    return months * 21


def _days_between(calendar: list[str], start: str, end: str) -> int:
    return bisect.bisect_right(calendar, end) - bisect.bisect_left(calendar, start)


def validate(form: Any, catalog: dict[str, Any]) -> list[FieldError]:
    """Every problem with the form, in field order (empty: valid)."""
    if not isinstance(form, dict):
        return [FieldError("dataset", "The form is missing.")]
    errors: list[FieldError] = []
    unknown = sorted(set(form) - set(FIELDS))
    if unknown:
        errors.append(FieldError("dataset", f"Unknown field(s): {', '.join(unknown)}."))
    datasets = {d["id"]: d for d in catalog.get("datasets", [])}
    ds = datasets.get(form.get("dataset"))  # type: ignore[arg-type]
    if ds is None:
        errors.append(FieldError("dataset", "Choose one of the available datasets."))
    strategy = form.get("strategy")
    spec = STRATEGIES.get(strategy) if isinstance(strategy, str) else None
    if spec is None:
        errors.append(FieldError("strategy", "Choose a strategy."))

    params = form.get("params")
    params_ok = False
    if spec is not None:
        if not isinstance(params, dict):
            errors.append(FieldError("params", "Choose at least one value for every parameter."))
        else:
            params_ok = True
            for extra in sorted(set(params) - set(spec["params"])):
                errors.append(FieldError(f"params.{extra}", f"{strategy} has no parameter {extra}."))
                params_ok = False
            for name, allowed in spec["params"].items():
                chosen = params.get(name)
                if not isinstance(chosen, list) or not chosen:
                    errors.append(FieldError(f"params.{name}", "Choose at least one value."))
                    params_ok = False
                elif any(isinstance(v, bool) or v not in allowed for v in chosen) or len(set(chosen)) != len(chosen):
                    errors.append(FieldError(f"params.{name}", f"Choose from {', '.join(map(str, allowed))}."))
                    params_ok = False
            if params_ok:
                size = 1
                for name in spec["params"]:
                    size *= len(params[name])
                if size > MAX_GRID:
                    errors.append(FieldError("params", f"That is {size} combinations; choose at most {MAX_GRID}."))
                    params_ok = False

    start, end = form.get("start"), form.get("end")
    dates_ok = True
    for name, value in (("start", start), ("end", end)):
        if not isinstance(value, str) or not _DATE.match(value):
            errors.append(FieldError(name, "Use a date like 2015-01-02."))
            dates_ok = False
    if dates_ok and ds is not None:
        assert isinstance(start, str) and isinstance(end, str)
        if start < ds["start"]:
            errors.append(FieldError("start", f"The data starts on {ds['start']}."))
            dates_ok = False
        if end > ds["end"]:
            errors.append(FieldError("end", f"The data ends on {ds['end']}."))
            dates_ok = False
        if dates_ok and start >= end:
            errors.append(FieldError("end", "The end must be after the start."))
            dates_ok = False

    symbols = form.get("symbols")
    if ds is not None:
        by_symbol = {s["symbol"]: s for s in ds["symbols"]}
        if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) for s in symbols):
            errors.append(FieldError("symbols", "Choose at least one symbol."))
        elif len(set(symbols)) != len(symbols):
            errors.append(FieldError("symbols", "A symbol is listed twice."))
        elif any(s not in by_symbol for s in symbols):
            missing = [s for s in symbols if s not in by_symbol]
            errors.append(FieldError("symbols", f"Not in this dataset: {', '.join(missing)}."))
        else:
            if spec is not None and len(symbols) < spec["min_symbols"]:
                errors.append(FieldError("symbols", f"{spec['label']} ranks symbols against each other: "
                                                    f"choose at least {spec['min_symbols']}."))
            if dates_ok:
                empty = [s for s in symbols if by_symbol[s]["last"] < start or by_symbol[s]["first"] > end]
                if empty:
                    errors.append(FieldError("symbols", f"No data in the chosen dates for {', '.join(empty)}."))

    train_years = form.get("train_years")
    if isinstance(train_years, bool) or train_years not in TRAIN_YEARS:
        errors.append(FieldError("train_years", "Choose 1 to 5 years of training."))
        train_years = None
    if not isinstance(form.get("costs"), bool):
        errors.append(FieldError("costs", "Choose whether to include costs."))

    if ds is not None and dates_ok and train_years is not None:
        assert isinstance(start, str) and isinstance(end, str)
        have = _days_between(ds["calendar"], start, end)
        need = train_years * DAYS_PER_YEAR + TEST_DAYS
        if have < need:
            errors.append(FieldError("range", f"These dates hold {have} trading days; walk-forward with "
                                              f"{train_years} year(s) of training and 1 year of testing needs "
                                              f"at least {need}. Start earlier or train for fewer years."))
        if params_ok and spec is not None and isinstance(params, dict):
            needed = lookback_days(str(strategy), params)
            if needed >= train_years * DAYS_PER_YEAR:
                errors.append(FieldError("train_years", f"The longest lookback needs {needed} trading days of "
                                                        f"history, more than {train_years} year(s) of training "
                                                        f"({train_years * DAYS_PER_YEAR} days)."))
    return errors


def build_config(form: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """The walk-forward config for a valid form, or FormInvalid."""
    errors = validate(form, catalog)
    if errors:
        raise FormInvalid(errors)
    ds = next(d for d in catalog["datasets"] if d["id"] == form["dataset"])
    data: dict[str, Any] = {"start": form["start"], "end": form["end"], "symbols": sorted(form["symbols"])}
    if "synthetic_bars" in ds:
        data["synthetic_bars"] = ds["synthetic_bars"]
    else:
        data["bars"] = ds["path"]
    if ds.get("rates"):
        data["rates"] = ds["rates"]
    benchmark = ds.get("benchmark")
    params = STRATEGIES[form["strategy"]]["params"]
    return {
        "kind": "walkforward",
        "seed": 0,
        "data": data,
        "execution": {"capital": CAPITAL, "lag": 1, "price": "open", "liquidate_at_end": True},
        # Values in the allowed order, so the same choice always gives the same grid.
        "strategy": {"name": form["strategy"],
                     "grid": {k: [v for v in params[k] if v in form["params"][k]] for k in sorted(params)}},
        "walkforward": {"train": form["train_years"] * DAYS_PER_YEAR, "test": TEST_DAYS},
        "costs": COSTS if form["costs"] else {},
        "analytics": {"benchmark": benchmark if benchmark in form["symbols"] else sorted(form["symbols"])[0]},
    }
