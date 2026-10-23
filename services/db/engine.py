"""The async SQLAlchemy engine. The URL carries the password from PGPASSWORD; SQLAlchemy
masks it in reprs and logs."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.common.config import Settings


def make_engine(settings: Settings, **kw: object) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True, **kw)  # type: ignore[arg-type]
