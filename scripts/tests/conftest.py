"""Shared fixtures for the scripts tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "id(*ids): TEST_PLAN ID(s) this test proves, e.g. id('VERCEL-10')"
    )


@pytest.fixture
def harness(tmp_path: Path):
    from deploy_harness import Harness

    return Harness(tmp_path)


def _to_async(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _migrate(url: str) -> None:
    env = {**os.environ, "DATABASE_DIRECT_URL": url, "DATABASE_URL": url}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "apps" / "api",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    """A migrated Postgres 16 (TEST_DATABASE_URL, or a Testcontainer like the API tests use)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        _migrate(url)
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        url = _to_async(pg.get_connection_url())
        _migrate(url)
        yield url
