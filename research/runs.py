"""Reproducible backtest runs (M8).

A run is described by a config (TOML on the command line, a dict here). ``run`` checks it
strictly, loads or generates the data, runs it, and stores in ``<out>/<kind>-<id>/``:

- ``results.json``: everything the run produced, canonical JSON (fixed key order and
  float formatting), so equal results are equal bytes;
- ``record.json``: the canonical config (defaults filled in), each dataset's content
  hash, the seed, the assumptions (fill rules, queue and liquidity models, cost model,
  survivorship), the engine (version, build type, invariant checks), the git commit and
  whether the tree was dirty, library versions, platform, and the SHA-256 of
  ``results.json``.

``reproduce`` re-runs a record: it refuses if a dataset's hash or the engine version
differs, and reports whether the new ``results.json`` is byte-identical. Generated data
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

from research.backtest.bars import BarPanel
from research.backtest.causality import check_causal
from research.backtest.costs import COST_MODELS
from research.backtest.event import EventConfig, run_event_backtest
from research.backtest.metrics import summary
from research.backtest.vectorized import BarExecution, run_vectorized
from research.backtest.walkforward import make_folds, walk_forward
from research.data import store
from research.sim.bars import generate_bars
from research.sim.market import SynthConfig, generate
from research.strategies.obmom import OrderBookMomentum
from research.strategies.tsmom import TimeSeriesMomentum
from research.strategies.xsmom import CrossSectionalMomentum

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORD_VERSION = 1
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
                "bars": {"kind", "seed", "data", "strategy", "execution", "costs"},
                "walkforward": {"kind", "seed", "data", "strategy", "execution", "walkforward", "costs"}}
    if kind not in sections:
        raise ConfigError(f"kind must be one of {sorted(sections)}, not {kind!r}")
    unknown = set(cfg) - sections[kind]
    if unknown:
        raise ConfigError(f"unknown top-level key(s) for kind={kind}: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {"kind": kind, "seed": cfg.get("seed", 0)}
    if not isinstance(out["seed"], int):
        raise ConfigError("seed must be an integer")
    out["costs"] = _section(cfg, "costs", {"model": "zero"})
    if out["costs"]["model"] not in COST_MODELS:
        raise ConfigError(f"[costs] model must be one of {sorted(COST_MODELS)} (M8 reports gross results)")

    data = cfg.get("data")
    if not isinstance(data, dict) or len(data) == 0:
        raise ConfigError("[data] is required")
    if kind == "event":
        if set(data) not in ({"synthetic"}, {"quotes", "trades"}):
            raise ConfigError("[data] for an event run: either synthetic = {...} or quotes = path, trades = path")
        if "synthetic" in data:
            try:
                SynthConfig.from_dict(data["synthetic"])
            except (TypeError, ValueError) as err:
                raise ConfigError(f"[data.synthetic]: {err}") from None
    else:
        if set(data) not in ({"synthetic_bars"}, {"bars"}):
            raise ConfigError("[data] for a bar run: either synthetic_bars = {...} or bars = path")
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
        out["execution"] = _section(cfg, "execution", {"capital": 1_000_000_000_000, "lag": 1, "price": "open"})
        try:
            BarExecution(out["execution"]["lag"], out["execution"]["price"])
        except ValueError as err:
            raise ConfigError(f"[execution] {err}") from None
        if not isinstance(out["execution"]["capital"], int) or out["execution"]["capital"] <= 0:
            raise ConfigError("[execution] capital must be a positive integer (micro-dollars)")
    if kind == "walkforward":
        out["walkforward"] = _section(cfg, "walkforward", {"step": None, "anchored": False}, required=("train", "test"))
    return out


canonical_config = validate_config


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
        return entries, {**frames, "survivorship": "synthetic: one simulated instrument"}
    if "synthetic_bars" in data:
        params = dict(data["synthetic_bars"])
        df = generate_bars(**params)
        meta = {"source": "synthetic", "generator": "research.sim.bars", "params": params}
        entry = {"name": "bars", "path": None, "generated": params, "sha256": store.content_hash(df, "bars", meta),
                 "rows": len(df), "survivorship": "synthetic: no symbol ever delists"}
        return [entry], {"bars": df, "survivorship": entry["survivorship"]}
    entries, frames = [], {}
    for name in ("quotes", "trades", "bars"):
        if name in data:
            ds = store.load(data[name], name)
            entry = {"name": name, "path": str(data[name]), "generated": None, "sha256": ds.sha256, "rows": len(ds.df)}
            if name == "bars":
                entry["survivorship"] = ds.meta.get("survivorship", "unknown: not stated by the dataset")
                frames["survivorship"] = entry["survivorship"]
            entries.append(entry)
            frames[name] = ds.df
    frames.setdefault("survivorship", "recorded single instrument")
    return entries, frames


# --- Execution ----------------------------------------------------------------------------------

def _num(x: float) -> float:
    """Floats in results are rounded to 12 significant digits: equal runs give equal
    text even if a platform's last-bit float behaviour differs."""
    return float(f"{x:.12g}")


def _run_event(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path) -> tuple[dict, dict]:
    strategy = EVENT_STRATEGIES[cfg["strategy"]["name"]](**cfg["strategy"]["params"])
    journal = run_dir / "journal.mfj"
    r = run_event_backtest(frames["quotes"], frames["trades"], strategy, _event_config(cfg), journal_path=journal)
    results = {
        "fills": [[f.ts, f.client_id, f.side.name, f.price, f.qty, f.liquidity.name, f.leaves_qty] for f in r.fills],
        "orders": len({u.client_id for u in r.updates} | {f.client_id for f in r.fills}),
        "rejects": sorted({u.reason for u in r.updates if u.kind == "reject"}),
        "position": r.position, "cash": r.cash, "mark": r.mark, "pnl": r.pnl,
        "commands": r.commands, "events_sha256": r.events_sha256,
    }
    return results, {**r.assumptions, "cost_model": cfg["costs"]["model"]}


def _bar_assumptions(cfg: dict[str, Any], frames: dict[str, Any]) -> dict[str, Any]:
    return {"fill_lag": cfg["execution"]["lag"], "fill_price": cfg["execution"]["price"],
            "signal": "close of bar t, from bars <= t", "sizing": "trunc(w * capital / close[t]) shares, fixed capital",
            "rebalance": "when a symbol's weight changes", "cost_model": cfg["costs"]["model"],
            "survivorship": frames["survivorship"], "causality_check_cuts": CAUSALITY_CUTS}


def _cuts(n: int) -> list[int]:
    return sorted({int(x) for x in np.linspace(n // 4, n - 2, CAUSALITY_CUTS)})


def _run_bars(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path) -> tuple[dict, dict]:
    panel = BarPanel.from_frame(frames["bars"])
    strategy = BAR_STRATEGIES[cfg["strategy"]["name"]](**cfg["strategy"]["params"])
    check_causal(strategy, panel, _cuts(len(panel.dates)))
    ex = cfg["execution"]
    r = run_vectorized(panel, strategy.weights(panel), ex["capital"], BarExecution(ex["lag"], ex["price"]),
                       COST_MODELS[cfg["costs"]["model"]]())
    results = {
        "symbols": list(panel.symbols),
        "metrics": {k: _num(v) for k, v in summary(r.net_pnl_per_bar, ex["capital"]).items()},
        "gross_pnl": r.gross_pnl, "net_pnl": r.net_pnl, "costs": r.costs,
        "equity": r.equity.tolist(),
        "trades": [[t.bar, t.symbol, t.qty, t.price] for t in r.trades],
    }
    return results, _bar_assumptions(cfg, frames)


def _run_walkforward(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path) -> tuple[dict, dict]:
    panel = BarPanel.from_frame(frames["bars"])
    factory = BAR_STRATEGIES[cfg["strategy"]["name"]]
    grid = _grid(cfg["strategy"]["grid"])
    for params in grid:
        check_causal(factory(**params), panel, _cuts(len(panel.dates)))
    ex, wf = cfg["execution"], cfg["walkforward"]
    folds = make_folds(len(panel.dates), wf["train"], wf["test"], wf["step"], wf["anchored"])
    r = walk_forward(panel, factory, grid, folds, ex["capital"], BarExecution(ex["lag"], ex["price"]),
                     COST_MODELS[cfg["costs"]["model"]]())
    results = {
        "symbols": list(panel.symbols),
        "folds": [{"train": [str(panel.dates[f.fold.train_start]), str(panel.dates[f.fold.train_end - 1])],
                   "test": [str(panel.dates[f.fold.test_start]), str(panel.dates[f.fold.test_end - 1])],
                   "params": f.params} for f in r.folds],
        "oos_metrics": {k: _num(v) for k, v in r.oos_metrics.items()},
        "oos_pnl": r.oos_pnl.tolist(),
    }
    assumptions = {**_bar_assumptions(cfg, frames), "selection": "max training Sharpe, first in grid order on ties",
                   "reported": "out-of-sample only", "grid_size": len(grid), "folds": len(folds)}
    return results, assumptions


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


def _execute(cfg: dict[str, Any], frames: dict[str, Any], run_dir: Path) -> tuple[bytes, dict[str, Any]]:
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    results, assumptions = RUNNERS[cfg["kind"]](cfg, frames, run_dir)
    body = _canonical_bytes(results)
    (run_dir / "results.json").write_bytes(body)
    return body, assumptions


def run(config: dict[str, Any], *, out_dir: str | Path = "runs", name: str | None = None) -> Record:
    """Run ``config`` into ``<out_dir>/<name>`` (default ``<kind>-<run id>``); the run id
    is a hash of the canonical config and the data hashes."""
    cfg = validate_config(config)
    entries, frames = _load_data(cfg)
    run_id = hashlib.sha256(_canonical_bytes({"config": cfg, "data": [e["sha256"] for e in entries]})).hexdigest()[:12]
    run_dir = Path(out_dir) / (name or f"{cfg['kind']}-{run_id}")
    body, assumptions = _execute(cfg, frames, run_dir)
    record = {"record_version": RECORD_VERSION, "run_id": run_id, "kind": cfg["kind"], "seed": cfg["seed"],
              "config": cfg, "data": entries, "assumptions": assumptions, **_environment(),
              "results_file": "results.json", "results_sha256": hashlib.sha256(body).hexdigest()}
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
    body, _ = _execute(cfg, frames, run_dir)
    sha = hashlib.sha256(body).hexdigest()
    return Reproduction(identical=sha == record["results_sha256"], results_path=run_dir / "results.json",
                        results_sha256=sha, stored_sha256=record["results_sha256"], warnings=warnings)


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
