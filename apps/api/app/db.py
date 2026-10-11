"""Async engine and session factory."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def make_engine(
    database_url: str, *, pool_size: int = 10, serverless: bool = False, serverless_pool_size: int = 0
) -> AsyncEngine:
    """`serverless` (PROFILE=vercel): no prepared-statement caches, because Neon's pooled URL is
    PgBouncer in transaction mode. `serverless_pool_size` > 0 keeps that many connections per
    instance (ADR-0032): a fresh connection per request cost ~120 ms of setup and per-connection
    type lookups, some of it inside the house lock (7 ticks/s instead of 58, SYNC-24). 0 = open a
    connection per session (NullPool), for code that may run in a new event loop each time."""
    server_settings = {"timezone": "UTC", "application_name": "kitchentab-api"}
    if serverless:
        pooling: dict[str, Any] = (
            {
                "pool_size": serverless_pool_size,
                "max_overflow": serverless_pool_size // 2,
                "pool_pre_ping": True,  # a frozen instance may wake up with dead connections
                "pool_recycle": 300,
            }
            if serverless_pool_size > 0
            else {"poolclass": NullPool}
        )
        return create_async_engine(
            database_url,
            **pooling,
            connect_args={
                "server_settings": server_settings,
                "statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__kt_{uuid.uuid4().hex}__",
            },
        )
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=pool_size * 2,
        pool_pre_ping=True,
        connect_args={"server_settings": server_settings},
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def make_read_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Views that read several tables (a bill, its items, allocations, fees) see one snapshot: under
    READ COMMITTED a write committing between two SELECTs gives a torn view (an old total next to new
    prices), which can't reconcile (SYNC-35). Read-only REPEATABLE READ never raises serialization
    failures in Postgres, and BEGIN carries the level, so it also works through PgBouncer."""
    reads = engine.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
    return async_sessionmaker(reads, expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
