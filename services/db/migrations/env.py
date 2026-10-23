"""Alembic environment (M10): async (asyncpg). The URL comes from the caller
(services/db/migrate.py) through config.attributes, never from a file."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from services.db.models import metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(context.config.attributes["url"])
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


asyncio.run(_main())
