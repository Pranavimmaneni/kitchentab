"""`make restore-drill` (TEST_PLAN DATA-02): seed a scratch database, export it, restore the export
into a second fresh database, compare every row and run the invariant checker on the copy.

Runs against the Postgres server in DATABASE_URL (local compose, or a Neon branch's direct URL),
creates `drill_src_*` and `drill_restore_*` databases and always drops them again.

    uv run python -m app.tools.restore_drill [--export-file PATH] [--keep]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import make_engine, make_sessionmaker
from app.seed.scenario import load_scenario
from app.seed.writer import write_scenario
from app.services import backup
from app.services.invariants import check_all
from app.settings import asyncpg_url

API_DIR = Path(__file__).resolve().parents[2]
DRILL_PEPPER = "restore-drill-pepper-0123456789abcdef"


def _alembic(url: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["skip_logging_config"] = True
    return cfg


def with_database(url: str, name: str) -> str:
    """The same server URL pointing at another database, query (e.g. `ssl=require`) kept."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def _create(admin: AsyncEngine, base: str, prefix: str) -> str:
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    async with admin.connect() as conn:
        await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(text(f'CREATE DATABASE "{name}"'))
    url = with_database(base, name)
    await asyncio.to_thread(command.upgrade, _alembic(url), "head")
    return name


async def _drop(admin: AsyncEngine, name: str) -> None:
    async with admin.connect() as conn:
        auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await auto.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


async def drill(server_url: str, export_file: Path | None, keep: bool) -> int:
    server_url = asyncpg_url(server_url)
    base = server_url
    admin = make_engine(server_url, pool_size=1)
    now = datetime.now(UTC)
    created: list[str] = []
    try:
        src = await _create(admin, base, "drill_src")
        created.append(src)
        source = make_engine(with_database(base, src), pool_size=2)
        async with make_sessionmaker(source)() as db, db.begin():
            await write_scenario(db, load_scenario("apt4b"), DRILL_PEPPER)
        async with source.connect() as conn, conn.begin():
            data, meta = await backup.export(conn, now)
        await source.dispose()
        if export_file:
            await asyncio.to_thread(_write, export_file, data)
        print(
            f"exported {sum(meta['tables'].values())} rows from {len(meta['tables'])} tables, "
            f"{meta['bytes']} bytes, sha256 {meta['sha256'][:16]}"
        )

        dst = await _create(admin, base, "drill_restore")
        created.append(dst)
        target = make_engine(with_database(base, dst), pool_size=2)
        async with target.begin() as conn:
            counts = await backup.restore(conn, data)
        async with target.connect() as conn, conn.begin():
            again, _ = await backup.export(conn, now)
        same = gzip.decompress(again).splitlines()[1:] == gzip.decompress(data).splitlines()[1:]
        async with make_sessionmaker(target)() as db:
            violations = await check_all(db, now)
        await target.dispose()
        print(f"restored {sum(counts.values())} rows; identical: {same}; invariant violations: {len(violations)}")
        for v in violations:
            print(f"  - {v}")
        ok = same and not violations
        print("RESTORE DRILL PASSED" if ok else "RESTORE DRILL FAILED")
        return 0 if ok else 1
    finally:
        if not keep:
            for name in created:
                await _drop(admin, name)
        await admin.dispose()


def _from_settings() -> str | None:
    """The same .env the API reads (pydantic-settings), so `make` needs no exported variables."""
    try:
        from app.settings import get_settings

        settings = get_settings()
    except Exception:
        return None
    return settings.database_direct_url or settings.database_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export-file", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="keep the scratch databases for inspection")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL") or _from_settings()
    if not url:
        print("Set DATABASE_URL (or DATABASE_DIRECT_URL) to a Postgres server you can create databases on.")
        return 2
    return asyncio.run(drill(url, args.export_file, args.keep))


if __name__ == "__main__":
    sys.exit(main())
