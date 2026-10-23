"""Database setup (M10): `python -m services.db.migrate` upgrades to the newest Alembic
revision and seeds `instruments` from the instruments YAML. Run once per start by the
compose `migrate` service before the gateway, projector and worker.

`--roundtrip-check` upgrades in a scratch database, checks the result against
services/db/models.py (alembic check), downgrades, upgrades again, then drops it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from services.common.config import Settings
from services.common.instruments import load_instruments
from services.db.engine import make_engine
from services.db.models import instruments

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["url"] = url  # not set_main_option: the password must not pass through ini interpolation
    return cfg


async def seed(settings: Settings) -> int:
    if settings.instruments_file is None or not settings.instruments_file.is_file():
        return 0
    rows = [{"symbol": i.symbol, "tick_size": i.tick_size} for i in load_instruments(settings.instruments_file)]
    engine = make_engine(settings)
    async with engine.begin() as conn:
        stmt = insert(instruments).values(rows)
        await conn.execute(stmt.on_conflict_do_update(index_elements=["symbol"],
                                                      set_={"tick_size": stmt.excluded.tick_size}))
    await engine.dispose()
    return len(rows)


async def _admin(settings: Settings, sql: str) -> None:
    engine = make_engine(settings, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


def roundtrip(settings: Settings) -> None:
    scratch = f"mf_roundtrip_{os.getpid()}"
    asyncio.run(_admin(settings, f'CREATE DATABASE "{scratch}"'))
    try:
        cfg = alembic_config(Settings(**{**settings.__dict__, "pg_database": scratch}).database_url)
        command.upgrade(cfg, "head")
        command.check(cfg)  # the migrated schema equals services/db/models.py
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    finally:
        asyncio.run(_admin(settings, f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
    print("roundtrip ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.db.migrate")
    parser.add_argument("--roundtrip-check", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.roundtrip_check:
        roundtrip(settings)
        return 0
    command.upgrade(alembic_config(settings.database_url), "head")
    print(f"migrated; {asyncio.run(seed(settings))} instruments seeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
