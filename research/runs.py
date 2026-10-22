"""Reproducible backtest runs (M8).

A run is described by a config (TOML on the command line, a dict here). ``run`` checks it
strictly, loads or generates the data, runs it, and stores in ``<out>/<kind>-<id>/``:

- ``results.json``: everything the run produced, canonical JSON (fixed key order and
  float formatting), so equal results are equal bytes;
- ``metrics.json`` and ``tearsheet.html`` (M9): the gross-vs-net report, portfolio-level
  only, and its HTML page rendered from it (``research.analytics``);
- ``record.json``: the canonical config (defaults filled in), each dataset's content
  hash, the seed, the assumptions (fill rules, queue and liquidity models, the cost
  model with every parameter, the risk-free rate, survivorship, the number of trials),
  the engine (version, build type, invariant checks), the git commit and whether the
  tree was dirty, library versions, platform, and the SHA-256 of each output file.

``reproduce`` re-runs a record: it refuses if a dataset's hash or the engine version
differs, and reports whether every output file is byte-identical. Generated data
(synthetic flow and bars) is regenerated from its stored parameters and must hash the
same too. No wall-clock time is stored anywhere, so a re-run can be identical.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mfengine
import numpy as np
import pandas as pd
import pyarrow

from research.analytics import report, tearsheet
from research.analytics.metrics import periods_per_year
from research.backtest.bars import BarPanel
from research.backtest.causality import check_causal
from research.backtest.costs import BAR, EVENT, CostModel, build_cost_model
from research.backtest.event import EventConfig, run_event_backtest
from research.backtest.metrics import summary
from research.backtest.rates import RiskFree
from research.backtest.vectorized import BarExecution, BarResult, run_vectorized
from research.backtest.walkforward import make_folds, walk_forward
from research.data.schema import BAR_PRICE_UNIT
from research.data import store
from research.sim.bars import generate_bars
from research.sim.market import SynthConfig, generate
from research.strategies.obmom import OrderBookMomentum
from research.strategies.tsmom import TimeSeriesMomentum
from research.strategies.xsmom import CrossSectionalMomentum

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORD_VERSION = 2  # 2: outputs (metrics.json, tearsheet.html) with their hashes; [costs] components
EVENT_STRATEGIES: dict[str, Any] = {"obmom": OrderBookMomentum}
BAR_STRATEGIES: dict[str, Any] = {"tsmom": TimeSeriesMomentum, "xsmom": CrossSectionalMomentum}
CAUSALITY_CUTS = 5


class ConfigError(ValueError):
    pass


class DataMismatch(RuntimeError):
    pass


class EngineMismatch(RuntimeError):
    pass


# --- Config ---------------------------------------------------------------------------------

def _section(cfg: dict[str, Any], name: str, defaults: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    given = cfg.get(name, {})
    if not isinstance(given, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = set(given) - set(defaults) - set(required)
    if unknown:
        raise ConfigError(f"[{name}]: unknown key(s) {', '.join(sorted(unknown))}")
    missing = [k for k in required if k not in given]
    if missing:
        raise ConfigError(f"[{name}]: missing {', '.join(missing)}")
    return {**defaults, **given}


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """The config with every default filled in, or ``ConfigError``. Unknown keys are
    errors: a typo must not silently fall back to a default."""
    cfg = copy.deepcopy(config)
    kind = cfg.get("kind")
    sections = {"event": {"kind", "seed", "data", "strategy", "broker", "replay", "risk", "costs"},
                "bars": {"kind", "seed", "data", "strategy", "execution", "costs", "analytics"},
                "walkforward": {"kind", "seed", "data", "strategy", "execution", "walkforward", "costs", "analytics"}}
    if kind not in sections:
        raise ConfigError(f"kind must be one of {sorted(sections)}, not {kind!r}")
    unknown = set(cfg) - sections[kind]
    if unknown:
        raise ConfigError(f"unknown top-level key(s) for kind={kind}: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {"kind": kind, "seed": cfg.get("seed", 0)}
    if not isinstance(out["seed"], int):
        raise ConfigError("seed must be an integer")
    costs = cfg.get("costs", {})
    if not isinstance(costs, dict):
        raise ConfigError("[costs] must be a table of components")
    try:
        build_cost_model(costs, EVENT if kind == "event" else BAR)
    except (TypeError, ValueError) as err:
        raise ConfigError(str(err)) from None
    out["costs"] = costs

    data = cfg.get("data")
    if not isinstance(data, dict) or len(data) == 0:
        raise ConfigError("[data] is required")
    if kind == "event":
        if "rates" in data:
            raise ConfigError("[data] rates (the risk-free rate) is for bar runs only: an event run is one session")
        if set(data) not in ({"synthetic"}, {"quotes", "trades"}):
            raise ConfigError("[data] for an event run: either synthetic = {...} or quotes = path, trades = path")
        if "synthetic" in data:
            try:
                SynthConfig.from_dict(data["synthetic"])
            except (TypeError, ValueError) as err:
                raise ConfigError(f"[data.synthetic]: {err}") from None
    else:
        if set(data) - {"rates"} not in ({"synthetic_bars"}, {"bars"}):
            raise ConfigError("[data] for a bar run: either synthetic_bars = {...} or bars = path "
                              "(and optionally rates = path, the risk-free rate)")
    out["data"] = data

    registry = EVENT_STRATEGIES if kind == "event" else BAR_STRATEGIES
    strat = cfg.get("strategy", {})
    if strat.get("name") not in registry:
        raise ConfigError(f"[strategy] name must be one of {sorted(registry)}")
    params_key = "grid" if kind == "walkforward" else "params"
    out["strategy"] = _section(cfg, "strategy", {params_key: {}}, required=("name",))
    try:
        for params in _grid(out["strategy"]["grid"]) if kind == "walkforward" else [out["strategy"]["params"]]:
            registry[strat["name"]](**params)
    except (TypeError, ValueError) as err:
        raise ConfigError(f"[strategy] {strat['name']}: {err}") from None

    if kind == "event":
        out["broker"] = _section(cfg, "broker", {"latency_ns": 0, "jitter_ns": 0})
        out["replay"] = _section(cfg, "replay", {"queue": "pessimistic", "consumed_liquidity": "stays_removed"})
        out["risk"] = _section(cfg, "risk", {"max_notional": None, "position_limit": None, "credit": None,
                                             "max_open_orders": None})
        out["risk"] = {k: v for k, v in out["risk"].items() if v is not None}
        try:
            _event_config(out)
        except ValueError as err:
            raise ConfigError(str(err)) from None
    else:
        out["execution"] = _section(cfg, "execution", {"capital": 1_000_000_000_000, "lag": 1, "price": "open",
                                                       "liquidate_at_end": True})
        if not isinstance(out["execution"]["liquidate_at_end"], bool):
            raise ConfigError("[execution] liquidate_at_end must be true or false")
        try:
            BarExecution(out["execution"]["lag"], out["execution"]["price"])
        except ValueError as err:
            raise ConfigError(f"[execution] {err}") from None
        if not isinstance(out["execution"]["capital"], int) or out["execution"]["capital"] <= 0:
            raise ConfigError("[execution] capital must be a positive integer (micro-dollars)")
        out["analytics"] = _analytics(cfg)
    if kind == "walkforward":
        out["walkforward"] = _section(cfg, "walkforward", {"step": None, "anchored": False}, required=("train", "test"))
    return out


canonical_config = validate_config


def _analytics(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        a = _section(cfg, "analytics", {"benchmark": None, "capacity": [], "periods_per_year": None})
    except ConfigError as err:
        raise ConfigError(str(err)) from None
    if a["benchmark"] is not None and not isinstance(a["benchmark"], str):
        raise ConfigError("[analytics] benchmark must be a symbol name")
    cap = a["capacity"]
    if not isinstance(cap, list) or not all(isinstance(c, int) and not isinstance(c, bool) and c > 0 for c in cap) \
            or any(b <= a_ for a_, b in zip(cap, cap[1:])):
        raise ConfigError("[analytics] capacity must be increasing positive integers (capital in micro-dollars)")
    ppy = a["periods_per_year"]
    if ppy is not None and (not isinstance(ppy, int) or isinstance(ppy, bool) or ppy <= 0):
        raise ConfigError("[analytics] periods_per_year must be a positive integer")
    return a


def _grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Every combination, keys in sorted order, values in the order given."""
    if not grid:
        raise ConfigError("[strategy.grid] is empty")
    keys = sorted(grid)
    for k in keys:
        if not isinstance(grid[k], list) or not grid[k]:
            raise ConfigError(f"[strategy.grid] {k} must be a non-empty list")
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


def _event_config(cfg: dict[str, Any]) -> EventConfig:
    risk = mfengine.AccountLimits(**cfg["risk"]) if cfg["risk"] else None
    return EventConfig(latency_ns=cfg["broker"]["latency_ns"], jitter_ns=cfg["broker"]["jitter_ns"], seed=cfg["seed"],
                       queue=cfg["replay"]["queue"], consumed_liquidity=cfg["replay"]["consumed_liquidity"], risk=risk)


# --- Data -------------------------------------------------------------------------------------

def _load_data(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(record entries, frames by name)."""
    data = cfg["data"]
    if "synthetic" in data:
        synth = SynthConfig.from_dict(data["synthetic"])
        result = generate(synth)
        generated = synth.to_dict()
        frames = {"quotes": result.quotes, "trades": result.trades}
        entries = [{"name": name, "path": None, "generated": generated,
                    "sha256": store.content_hash(df, name, result.meta), "rows": len(df)}
                   for name, df in frames.items()]
        return entries, {**frames, "survivorship": "synthetic: one simulated instrument",
                         "tick_size": result.meta["instrument"]["tick_size"]}
    if "synthetic_bars" in data:
        params = dict(data["synthetic_bars"])
        df = generate_bars(**params)
        meta = {"source": "synthetic", "generator": "research.sim.bars", "params": params}
        entry = {"name": "bars", "path": None, "generated": params, "sha256": store.content_hash(df, "bars", meta),
                 "rows": len(df), "survivorship": "synthetic: no symbol ever delists"}
        entries, frames = [entry], {"bars": df, "survivorship": entry["survivorship"]}
    else:
        entries, frames = [], {}
        for name in ("quotes", "trades", "bars"):
            if name in data:
                ds = store.load(data[name], name)
                entry = {"name": name, "path": str(data[name]), "generated": None, "sha256": ds.sha256,
                         "rows": len(ds.df)}
                if name == "bars":
                    entry["survivorship"] = ds.meta.get("survivorship", "unknown: not stated by the dataset")
                    frames["survivorship"] = entry["survivorship"]
                else:
                    frames["tick_size"] = ds.meta.get("instrument", {}).get("tick_size", "1")
                entries.append(entry)
                frames[name] = ds.df
        frames.setdefault("survivorship", "recorded single instrument")
    if "rates" in data:
        ds = store.load(data["rates"], "rates")
        entries.append({"name": "rates", "path": str(data["rates"]), "generated": None, "sha256": ds.sha256,
                        "rows": len(ds.df)})
        frames["rates"] = RiskFree.from_frame(ds.df, source=f"FRED DTB3 ({data['rates']})")
    return entries, frames


# --- Execution ----------------------------------------------------------------------------------

def _num(x: float) -> float:
    """Floats in results are rounded to 12 significant digits: equal runs give equal
    text even if a platform's last-bit float behaviour differs."""
    return float(f"{x:.12g}")


def _run_event(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path,
               entries: list[dict[str, Any]]) -> tuple[dict, dict, dict]:
    strategy = EVENT_STRATEGIES[cfg["strategy"]["name"]](**cfg["strategy"]["params"])
    model = build_cost_model(cfg["costs"], EVENT)
    journal = run_dir / "journal.mfj"
    r = run_event_backtest(frames["quotes"], frames["trades"], strategy, _event_config(cfg), journal_path=journal,
                           cost_model=model, tick_size=frames["tick_size"])
    orders = len({u.client_id for u in r.updates} | {f.client_id for f in r.fills})
    results = {
        "fills": [[f.ts, f.client_id, f.side.name, f.price, f.qty, f.liquidity.name, f.leaves_qty] for f in r.fills],
        "orders": orders,
        "rejects": sorted({u.reason for u in r.updates if u.kind == "reject"}),
        "position": r.position, "cash": r.cash, "mark": r.mark, "pnl": r.pnl,
        "costs": r.costs, "net_pnl": r.net_pnl,
        "commands": r.commands, "events_sha256": r.events_sha256,
    }
    metrics = report.event_metrics(result=r, strategy=_strategy_info(cfg), data=_data_info(entries), cost_model=model,
                                   tick_size=frames["tick_size"], orders=orders)
    return results, {**r.assumptions, "costs": model.describe()}, metrics


def _strategy_info(cfg: dict[str, Any], folds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    info = {"name": cfg["strategy"]["name"]}
    info.update({"grid": cfg["strategy"]["grid"]} if "grid" in cfg["strategy"] else {"params": cfg["strategy"]["params"]})
    if folds is not None:
        info["folds"] = folds
    return info


def _data_info(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: e[k] for k in ("name", "path", "sha256", "rows") if k in e}
            | ({"survivorship": e["survivorship"]} if "survivorship" in e else {}) for e in entries]


def _bar_setup(cfg: dict[str, Any], frames: dict[str, Any]) -> tuple[BarPanel, CostModel, RiskFree, int, str | None]:
    panel = BarPanel.from_frame(frames["bars"])
    model = build_cost_model(cfg["costs"], BAR)
    rf = frames.get("rates", RiskFree.zero())
    a = cfg["analytics"]
    periods = a["periods_per_year"] or periods_per_year(panel.dates)
    bench = a["benchmark"]
    if bench is not None and bench not in panel.symbols:
        raise ConfigError(f"[analytics] benchmark {bench} is not in the data ({', '.join(panel.symbols)})")
    return panel, model, rf, periods, bench


def _bar_assumptions(cfg: dict[str, Any], frames: dict[str, Any], model: CostModel, rf: RiskFree,
                     trials: dict[str, int]) -> dict[str, Any]:
    return {"fill_lag": cfg["execution"]["lag"], "fill_price": cfg["execution"]["price"],
            "signal": "close of bar t, from bars <= t", "sizing": "trunc(w * capital / close[t]) shares, fixed capital",
            "rebalance": "when a symbol's weight changes", "costs": model.describe(),
            "bar_fills": "take liquidity (taker fees), at the print plus the modeled half-spread",
            "liquidate_at_window_end": cfg["execution"]["liquidate_at_end"],
            "risk_free": rf.describe(), "trials": trials,
            "survivorship": frames["survivorship"], "causality_check_cuts": CAUSALITY_CUTS}


def _cuts(n: int) -> list[int]:
    return sorted({int(x) for x in np.linspace(n // 4, n - 2, CAUSALITY_CUTS)})


def _execution(cfg: dict[str, Any]) -> dict[str, Any]:
    ex = cfg["execution"]
    return {"capital": ex["capital"], "lag": ex["lag"], "price": ex["price"], "liquidate_at_end": ex["liquidate_at_end"]}


def _run_bars(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path,
              entries: list[dict[str, Any]]) -> tuple[dict, dict, dict]:
    panel, model, rf, periods, bench = _bar_setup(cfg, frames)
    strategy = BAR_STRATEGIES[cfg["strategy"]["name"]](**cfg["strategy"]["params"])
    check_causal(strategy, panel, _cuts(len(panel.dates)))
    ex = cfg["execution"]
    w = strategy.weights(panel)

    def at(capital: int) -> BarResult:
        return run_vectorized(panel, w, capital, BarExecution(ex["lag"], ex["price"]), model, risk_free=rf,
                              liquidate_at_end=ex["liquidate_at_end"])

    r = at(ex["capital"])
    capacity = [report.capacity_point(c, [at(c)], periods) for c in cfg["analytics"]["capacity"]]
    trials = {"grid_size": 1, "folds": 0, "backtests": 1, "dsr_trials": 1, "capacity_points": len(capacity)}
    results = {
        "symbols": list(panel.symbols),
        "metrics": {k: _num(v) for k, v in summary(r.net_pnl_per_bar, ex["capital"]).items()},
        "gross_pnl": r.gross_pnl, "net_pnl": r.net_pnl, "costs": r.costs,
        "equity": r.equity.tolist(),
        "trades": [[t.bar, t.symbol, t.qty, t.price] for t in r.trades],
    }
    metrics = report.bar_metrics(kind="bars", runs=[r], panel=panel, capital=ex["capital"], periods=periods,
                                 cost_model=model, risk_free=rf, benchmark=bench, trials=trials, capacity=capacity,
                                 strategy=_strategy_info(cfg), data=_data_info(entries), execution=_execution(cfg))
    return results, _bar_assumptions(cfg, frames, model, rf, trials), metrics


def _run_walkforward(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path,
                     entries: list[dict[str, Any]]) -> tuple[dict, dict, dict]:
    panel, model, rf, periods, bench = _bar_setup(cfg, frames)
    factory = BAR_STRATEGIES[cfg["strategy"]["name"]]
    grid = _grid(cfg["strategy"]["grid"])
    for params in grid:
        check_causal(factory(**params), panel, _cuts(len(panel.dates)))
    ex, wf = cfg["execution"], cfg["walkforward"]
    folds = make_folds(len(panel.dates), wf["train"], wf["test"], wf["step"], wf["anchored"])

    def at(capital: int) -> Any:
        return walk_forward(panel, factory, grid, folds, capital, BarExecution(ex["lag"], ex["price"]), model,
                            risk_free=rf, liquidate_at_end=ex["liquidate_at_end"])

    r = at(ex["capital"])
    capacity = [report.capacity_point(c, list(at(c).oos_runs), periods) for c in cfg["analytics"]["capacity"]]
    trials = {"grid_size": len(grid), "folds": len(folds), "backtests": len(grid) * len(folds) + len(folds),
              "dsr_trials": len(grid), "capacity_points": len(capacity)}
    fold_info = [{"train": [str(panel.dates[f.fold.train_start]), str(panel.dates[f.fold.train_end - 1])],
                  "test": [str(panel.dates[f.fold.test_start]), str(panel.dates[f.fold.test_end - 1])],
                  "params": f.params} for f in r.folds]
    gross = np.concatenate([x.pnl for x in r.oos_runs])
    results = {
        "symbols": list(panel.symbols),
        "folds": fold_info,
        "oos_metrics": {k: _num(v) for k, v in r.oos_metrics.items()},
        "oos_pnl": r.oos_pnl.tolist(),
        "oos_gross_pnl": gross.tolist(),
    }
    assumptions = {**_bar_assumptions(cfg, frames, model, rf, trials),
                   "selection": "max training net excess Sharpe, first in grid order on ties",
                   "reported": "out-of-sample only", "grid_size": len(grid), "folds": len(folds)}
    metrics = report.bar_metrics(kind="walkforward", runs=list(r.oos_runs), panel=panel, capital=ex["capital"],
                                 periods=periods, cost_model=model, risk_free=rf, benchmark=bench, trials=trials,
                                 capacity=capacity, strategy=_strategy_info(cfg, fold_info), data=_data_info(entries),
                                 execution=_execution(cfg))
    return results, assumptions, metrics


RUNNERS = {"event": _run_event, "bars": _run_bars, "walkforward": _run_walkforward}


# --- Records ---------------------------------------------------------------------------------------

def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=1, ensure_ascii=True) + "\n").encode()


def _git() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit, "dirty": bool(status) if status is not None else None}


def _environment() -> dict[str, Any]:
    return {
        "engine": {"version": mfengine.version(), "build_type": mfengine.BUILD_TYPE,
                   "invariant_checks": mfengine.INVARIANT_CHECKS},
        "git": _git(),
        "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                     "pyarrow": pyarrow.__version__},
        "platform": {"system": platform.system(), "machine": platform.machine()},
    }


@dataclass
class Record:
    path: Path
    data: dict[str, Any]


@dataclass
class Reproduction:
    identical: bool
    results_path: Path
    results_sha256: str
    stored_sha256: str
    warnings: list[str] = field(default_factory=list)
    outputs_identical: dict[str, bool] = field(default_factory=dict)


OUTPUTS = ("results.json", "metrics.json", "tearsheet.html")


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _symbols(frames: dict[str, Any]) -> list[str]:
    return sorted(set(frames["bars"]["symbol"])) if "bars" in frames else []


def _execute(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path,
             entries: list[dict[str, Any]]) -> tuple[dict[str, bytes], dict[str, Any]]:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    results, assumptions, metrics = RUNNERS[cfg["kind"]](cfg, frames, run_dir, entries)
    tearsheet.check_portfolio_level(metrics, _symbols(frames))
    outputs = {"results.json": _canonical_bytes(results), "metrics.json": _canonical_bytes(metrics),
               "tearsheet.html": tearsheet.render(metrics).encode()}
    for name, body in outputs.items():
        (run_dir / name).write_bytes(body)
    return outputs, assumptions


def run(config: dict[str, Any], *, out_dir: str | Path = "runs", name: str | None = None) -> Record:
    """Run ``config`` into ``<out_dir>/<name>`` (default ``<kind>-<run id>``); the run id
    is a hash of the canonical config and the data hashes."""
    cfg = validate_config(config)
    entries, frames = _load_data(cfg)
    run_id = hashlib.sha256(_canonical_bytes({"config": cfg, "data": [e["sha256"] for e in entries]})).hexdigest()[:12]
    run_dir = Path(out_dir) / (name or f"{cfg['kind']}-{run_id}")
    outputs, assumptions = _execute(cfg, frames, run_dir, entries)
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in outputs.items()}
    record = {"record_version": RECORD_VERSION, "run_id": run_id, "kind": cfg["kind"], "seed": cfg["seed"],
              "config": cfg, "data": entries, "assumptions": assumptions, **_environment(),
              "results_file": "results.json", "results_sha256": hashes["results.json"], "outputs": hashes}
    path = run_dir / "record.json"
    path.write_bytes(_canonical_bytes(record))
    return Record(path=path, data=record)


def reproduce(record_path: str | Path, *, work_dir: str | Path) -> Reproduction:
    record = json.loads(Path(record_path).read_text())
    env = _environment()
    if record["engine"]["version"] != env["engine"]["version"]:
        raise EngineMismatch(f"recorded with mfengine {record['engine']['version']}, this is {env['engine']['version']}")
    warnings = []
    for key in ("build_type", "invariant_checks"):
        if record["engine"][key] != env["engine"][key]:
            warnings.append(f"engine {key}: recorded {record['engine'][key]}, now {env['engine'][key]} "
                            "(does not change results)")
    if record["git"]["commit"] != env["git"]["commit"]:
        warnings.append(f"recorded at commit {record['git']['commit']}, now at {env['git']['commit']}")
    if record["platform"] != env["platform"]:
        warnings.append(f"recorded on {record['platform']}, now on {env['platform']}")
    cfg = validate_config(record["config"])
    entries, frames = _load_data(cfg)
    stored = {e["name"]: e["sha256"] for e in record["data"]}
    for e in entries:
        if stored.get(e["name"]) != e["sha256"]:
            raise DataMismatch(f"dataset {e['name']} ({e['path'] or 'generated'}) hashes to {e['sha256']}, "
                               f"recorded {stored.get(e['name'])}")
    run_dir = Path(work_dir) / record["run_id"]
    outputs, _ = _execute(cfg, frames, run_dir, entries)
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in outputs.items()}
    recorded = record.get("outputs", {"results.json": record["results_sha256"]})
    same = {k: hashes.get(k) == v for k, v in recorded.items()}
    return Reproduction(identical=all(same.values()), results_path=run_dir / "results.json",
                        results_sha256=hashes["results.json"], stored_sha256=record["results_sha256"],
                        warnings=warnings, outputs_identical=same)


# --- TOML -----------------------------------------------------------------------------------------

def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def to_toml(config: dict[str, Any], _prefix: str = "") -> str:
    """A config dict as TOML (scalars and lists first, then one table per dict). Keys
    whose value is None are left out: TOML has no null."""
    lines = [f"{k} = {_toml_value(v)}" for k, v in config.items() if not isinstance(v, dict) and v is not None]
    for k, v in config.items():
        if isinstance(v, dict):
            name = f"{_prefix}{k}"
            lines.append(f"\n[{name}]")
            lines.append(to_toml(v, name + ".").strip("\n"))
    return "\n".join(lines) + "\n"
