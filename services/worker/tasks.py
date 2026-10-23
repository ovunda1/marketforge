"""The backtest worker (M10): an arq worker in its own container, consuming the Redis job
queue the gateway fills (`POST /backtests`).

    arq services.worker.tasks.WorkerSettings

Each job runs `research.runs.run` in a thread (the engine releases the GIL in batches;
the event loop stays free for Redis), writes the run's outputs under MF_RUNS_DIR/<id>,
then its metrics and equity curve to Postgres, and streams progress on `bt:<id>`.

A killed worker leaves its job in the queue; arq runs it again once the job's
in-progress lock expires (arq's timeout + 10 s). A run longer than MF_BACKTEST_TIMEOUT_S
is marked failed. Backtests are deterministic and every write
is keyed by the run id, so the retry rewrites the same rows: one run, same metrics.
A run that fails (a bad config, missing data) is marked failed and not retried.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from arq import func
from arq.connections import RedisSettings

from services.common import keys
from services.common.config import Settings
from services.db import backtests
from services.db.engine import make_engine

log = logging.getLogger("mf.worker")
CHUNK = 500


def equity_points(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """The equity curve of a bar or walk-forward run (event runs have none)."""
    series = metrics.get("series")
    if not series:
        return []
    return [{"idx": i, "date": str(d), "net_cum_return": float(n), "gross_cum_return": float(g),
             "net_drawdown": float(dd)}
            for i, (d, n, g, dd) in enumerate(zip(series["dates"], series["net_cum_return"],
                                                  series["gross_cum_return"], series["net_drawdown"]))]


async def run_backtest(ctx: dict[str, Any], run_id: str) -> str:
    from research import runs  # imported here: the module loads mfengine and the research stack

    settings: Settings = ctx["settings"]
    engine = ctx["engine"]
    redis = ctx["redis"]
    channel = keys.backtest_channel(run_id)
    row = await backtests.get(engine, run_id)
    if row is None:
        log.error("no backtest row %s", run_id)
        return "missing"
    if row["status"] in ("done", "failed"):
        return row["status"]
    attempt = await backtests.mark_running(engine, run_id)
    await redis.publish(channel, json.dumps({"type": "progress", "stage": "running", "attempt": attempt}))

    loop = asyncio.get_running_loop()

    def progress(stage: str, info: dict[str, Any]) -> None:
        msg = json.dumps({"type": "progress", "stage": stage, **info})
        asyncio.run_coroutine_threadsafe(redis.publish(channel, msg), loop)

    try:
        record = await asyncio.wait_for(
            asyncio.to_thread(runs.run, row["config"], out_dir=settings.runs_dir, name=run_id, progress=progress),
            settings.backtest_timeout_s)
    except TimeoutError:
        # Our own timeout, so the run is marked failed instead of staying "running" (arq's
        # is longer and only cancels). The thread cannot be killed; it ends on its own.
        message = f"timed out after {settings.backtest_timeout_s} s"
        await backtests.mark_failed(engine, run_id, message)
        await redis.publish(channel, json.dumps({"type": "failed", "id": run_id, "error": message}))
        return "failed"
    except Exception as e:  # noqa: BLE001 - recorded on the run, not retried
        log.warning("backtest %s failed: %s", run_id, e)
        await backtests.mark_failed(engine, run_id, f"{type(e).__name__}: {e}")
        await redis.publish(channel, json.dumps({"type": "failed", "id": run_id, "error": str(e)}))
        return "failed"
    metrics = json.loads((record.path.parent / "metrics.json").read_text())
    points = equity_points(metrics)
    await backtests.finish(engine, run_id, metrics, points, record.data.get("git", {}).get("commit"))
    for i in range(0, len(points), CHUNK):
        await redis.publish(channel, json.dumps({"type": "equity", "points": points[i:i + CHUNK]}))
    await redis.publish(channel, json.dumps({"type": "done", "id": run_id}))
    return "done"


async def startup(ctx: dict[str, Any]) -> None:
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    ctx["settings"] = Settings.from_env()
    ctx["engine"] = make_engine(ctx["settings"])


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


_settings = Settings.from_env()


# arq's timeout is above the run's own (which marks the run failed); arq reruns a job whose
# worker died once the job's lock expires: this timeout + 10 s.
ARQ_TIMEOUT_S = _settings.backtest_timeout_s + 60


class WorkerSettings:
    functions = [func(run_backtest, timeout=ARQ_TIMEOUT_S, max_tries=3)]  # type: ignore[arg-type]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = 2
    job_timeout = ARQ_TIMEOUT_S
