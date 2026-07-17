"""Async SQLAlchemy engine + session factory.

The engine is created lazily on first import. Tests can override `get_session`
via FastAPI dependency injection without touching this module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from evalgate.core.config import get_settings


def make_engine() -> AsyncEngine:
    settings = get_settings()
    kwargs: dict[str, object] = {"echo": False, "pool_pre_ping": True}
    # SQLite (aiosqlite / in-memory test DBs) rejects QueuePool sizing kwargs;
    # only tune the pool for real server databases.
    if not settings.database_url.startswith("sqlite"):
        kwargs.update(
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=1800,  # recycle < typical cloud idle-connection cutoff
        )
    return create_async_engine(settings.database_url, **kwargs)


engine: AsyncEngine = make_engine()

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
