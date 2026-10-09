"""Async engine and session factory."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def make_engine(database_url: str, *, pool_size: int = 10, serverless: bool = False) -> AsyncEngine:
    """`serverless` (PROFILE=vercel): no client-side pool and no prepared-statement caches, because
    Neon's pooled URL is PgBouncer in transaction mode and Vercel runs many short-lived instances."""
    server_settings = {"timezone": "UTC", "application_name": "kitchentab-api"}
    if serverless:
        return create_async_engine(
            database_url,
            poolclass=NullPool,
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


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
