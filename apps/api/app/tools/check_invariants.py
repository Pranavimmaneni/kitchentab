"""`scripts/check_invariants.py` (TEST_PLAN DATA-05): print every invariant violation in DATABASE_URL."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

from app.db import make_engine, make_sessionmaker
from app.services.invariants import check_all
from app.settings import asyncpg_url


async def run(url: str) -> int:
    engine = make_engine(url, pool_size=1)
    try:
        async with make_sessionmaker(engine)() as db:
            violations = await check_all(db, datetime.now(UTC))
    finally:
        await engine.dispose()
    for v in violations:
        print(v)
    print(f"{len(violations)} invariant violations")
    return 1 if violations else 0


def _from_settings() -> str | None:
    """The same .env the API reads (pydantic-settings), so `make` needs no exported variables."""
    try:
        from app.settings import get_settings

        settings = get_settings()
    except Exception:
        return None
    return settings.database_direct_url or settings.database_url


def main() -> int:
    url = os.environ.get("DATABASE_URL") or _from_settings()
    if not url:
        print("Set DATABASE_URL")
        return 2
    return asyncio.run(run(asyncpg_url(url)))


if __name__ == "__main__":
    sys.exit(main())
