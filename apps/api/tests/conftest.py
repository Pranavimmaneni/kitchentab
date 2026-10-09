"""Shared fixtures: real Postgres 16 and Redis (Testcontainers, or TEST_DATABASE_URL /
TEST_REDIS_URL when CI or `make test` provide them), migrated once per session and truncated
between tests. The app runs in-process with its real lifespan.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Test env before any app import: fakes on, test routes on (TEST_PLAN §2).
os.environ.update(
    {
        "APP_ENV": "test",
        "TEST_ROUTES_ENABLED": "true",
        "SESSION_SECRET": "test-session-secret-0123456789abcdef",
        "TOKEN_HASH_PEPPER": "test-pepper-0123456789abcdef0123",
        "EMAIL_PROVIDER": "fake",
        "SMS_PROVIDER": "fake",
        "EXTRACTOR_PROVIDER": "fake",
        "PUSH_PROVIDER": "fake",
        "LOG_LEVEL": "WARNING",
    }
)

import httpx
import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.clock import ControllableClock
from app.db import make_engine, make_sessionmaker
from app.main import create_app
from app.models import Base
from app.services.bus import RedisBus
from app.services.house_write import WriteContext
from app.settings import Settings, get_settings

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _to_async_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine", driver="asyncpg").with_command(
        "postgres -c track_commit_timestamp=on -c fsync=off -c max_connections=200"
    )
    with container as pg:
        yield _to_async_url(pg.get_connection_url())


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    url = os.environ.get("TEST_REDIS_URL")
    if url:
        yield url
        return
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as rc:
        yield f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0"


def alembic_config(database_url: str) -> Config:
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "migrations"))
    cfg.attributes["database_url"] = database_url
    cfg.attributes["skip_logging_config"] = True
    return cfg


@pytest.fixture(scope="session")
def migrated(database_url: str) -> str:
    cfg = alembic_config(database_url)

    def migrate() -> None:
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

    # Alembic's env.py uses asyncio.run(); this fixture may be resolved inside a running loop.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(migrate).result()
    return database_url


@pytest.fixture(scope="session")
def settings(migrated: str, redis_url: str) -> Settings:
    os.environ["DATABASE_URL"] = migrated
    os.environ["REDIS_URL"] = redis_url
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(settings.database_url, pool_size=30)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


@pytest.fixture(scope="session")
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def db_reset(engine: AsyncEngine, redis: Redis) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await redis.flushdb()


_STATEFUL = {"engine", "sessions", "redis", "writes", "app", "client", "seeded"}


@pytest.fixture(autouse=True)
def _clean_state(request: pytest.FixtureRequest) -> None:
    """Truncate every table and flush Redis before each test that touches them.

    Sync on purpose: async fixtures can't be requested dynamically from inside the running loop.
    """
    if _STATEFUL & set(request.fixturenames):
        request.getfixturevalue("db_reset")


@pytest.fixture
def clock() -> ControllableClock:
    return ControllableClock()


@pytest.fixture
def writes(
    sessions: async_sessionmaker[AsyncSession], clock: ControllableClock, redis: Redis
) -> WriteContext:
    return WriteContext(sessions, clock, RedisBus(redis))


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[Any]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c
