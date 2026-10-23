"""Service settings from the environment (M10). Secrets (the database password, the
Tiingo key) are read from the environment only: compose passes them from a git-ignored
`.env`; nothing here has a secret default."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    symbols: tuple[str, ...] = ()
    instruments_file: Path | None = None
    redis_url: str = "redis://localhost:6379/0"
    # libpq-style parts; the password comes from PGPASSWORD only.
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "marketforge"
    pg_database: str = "marketforge"
    pg_password: str = field(default="", repr=False)
    data_dir: Path = Path("/var/lib/marketforge/data")  # journals, intake logs, snapshots
    runs_dir: Path = Path("/var/lib/marketforge/runs")  # backtest outputs
    request_timeout_s: float = 2.0  # the gateway's wait for the matcher; also the request deadline
    ws_queue: int = 1000  # per WebSocket client, messages
    ws_max_overflows: int = 3  # within ws_overflow_window_s, then the client is closed
    ws_overflow_window_s: float = 30.0
    ws_send_timeout_s: float = 5.0
    rate_limit_per_s: int = 2000  # gateway requests per account per second
    snapshot_every: int = 10_000  # matcher snapshots, per symbol, in commands
    fsync: bool = True  # group commit: intake and journal fsynced before any reply
    depth: int = 50  # levels per side in published market data
    backtest_timeout_s: int = 1800

    @property
    def database_url(self) -> str:
        from urllib.parse import quote

        return (f"postgresql+asyncpg://{quote(self.pg_user)}:{quote(self.pg_password)}@{self.pg_host}:"
                f"{self.pg_port}/{quote(self.pg_database)}")

    @classmethod
    def from_env(cls) -> Settings:
        instruments_file = Path(_env("MF_INSTRUMENTS", "deploy/config/instruments.yaml"))
        symbols: tuple[str, ...] = ()
        if instruments_file.is_file():
            from services.common.instruments import load_instruments

            symbols = tuple(i.symbol for i in load_instruments(instruments_file))
        return cls(
            symbols=symbols,
            instruments_file=instruments_file,
            redis_url=_env("MF_REDIS_URL", cls.redis_url),
            pg_host=_env("PGHOST", cls.pg_host),
            pg_port=int(_env("PGPORT", str(cls.pg_port))),
            pg_user=_env("PGUSER", cls.pg_user),
            pg_database=_env("PGDATABASE", cls.pg_database),
            pg_password=os.environ.get("PGPASSWORD", ""),
            data_dir=Path(_env("MF_DATA_DIR", str(cls.data_dir))),
            runs_dir=Path(_env("MF_RUNS_DIR", str(cls.runs_dir))),
            request_timeout_s=float(_env("MF_REQUEST_TIMEOUT_S", str(cls.request_timeout_s))),
            ws_queue=int(_env("MF_WS_QUEUE", str(cls.ws_queue))),
            ws_max_overflows=int(_env("MF_WS_MAX_OVERFLOWS", str(cls.ws_max_overflows))),
            ws_overflow_window_s=float(_env("MF_WS_OVERFLOW_WINDOW_S", str(cls.ws_overflow_window_s))),
            ws_send_timeout_s=float(_env("MF_WS_SEND_TIMEOUT_S", str(cls.ws_send_timeout_s))),
            rate_limit_per_s=int(_env("MF_RATE_LIMIT_PER_S", str(cls.rate_limit_per_s))),
            snapshot_every=int(_env("MF_SNAPSHOT_EVERY", str(cls.snapshot_every))),
            fsync=_env("MF_FSYNC", "1") != "0",
            depth=int(_env("MF_DEPTH", str(cls.depth))),
            backtest_timeout_s=int(_env("MF_BACKTEST_TIMEOUT_S", str(cls.backtest_timeout_s))),
        )
