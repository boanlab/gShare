"""Async engine, sessionmaker, DeclarativeBase + naming convention.

This plane is the **only DB writer**. Credit isolation is READ COMMITTED with
per-wallet ``FOR UPDATE`` (handled in the domain layer, not here).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Stable constraint/index names so Alembic autogenerate diffs are deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        # READ COMMITTED is Postgres default; per-wallet FOR UPDATE serializes credit.
        # Pool sizing is explicit: the SQLAlchemy default (5+10 per process) starves under load.
        # SSE endpoints authenticate through get_sse_principal and hold no pooled connection for
        # the stream's lifetime, so the pool only has to cover request-scoped work.
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_pre_ping=True,
            future=True,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_recycle=1800,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an ``AsyncSession``."""
    async with get_sessionmaker()() as session:
        yield session
