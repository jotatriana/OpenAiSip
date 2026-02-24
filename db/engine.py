"""Async SQLAlchemy engine and session factory.

Switch databases by changing DATABASE_URL in .env:
  SQLite (dev):  sqlite+aiosqlite:///./openaisip.db
  PostgreSQL:    postgresql+asyncpg://user:pass@host/dbname
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config.settings import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    s = get_settings()
    connect_args = {"check_same_thread": False} if s.database_url.startswith("sqlite") else {}
    return create_async_engine(s.database_url, connect_args=connect_args, echo=False)


engine = _make_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all tables on first run."""
    from db import models as _  # noqa: F401 — ensure models are registered
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
