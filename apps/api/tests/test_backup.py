"""Backup export and restore drill (BUILD_ALL §4 "backup-restore drill"; TEST_PLAN DATA rows)."""

from __future__ import annotations

import gzip
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import make_engine, make_sessionmaker
from app.services import backup
from app.services.invariants import check_all
from tests.conftest import alembic_config
from tests.harness.api import Api
from tests.harness.tags import tid
from tests.test_kitchen import find, fridge, kitchen_world

ApiFactory = Callable[[], Api]
NOW = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)


async def _fresh_database(engine: AsyncEngine, database_url: str) -> str:
    name = f"restore_{uuid.uuid4().hex[:10]}"
    async with engine.connect() as conn:
        await (await conn.execution_options(isolation_level="AUTOCOMMIT")).execute(text(f'CREATE DATABASE "{name}"'))
    url = database_url.rsplit("/", 1)[0] + f"/{name}"
    with ThreadPoolExecutor(max_workers=1) as pool:  # Alembic's env.py runs its own event loop
        pool.submit(command.upgrade, alembic_config(url), "head").result()
    return url


def _body(data: bytes) -> list[str]:
    return gzip.decompress(data).decode().splitlines()[1:]  # rows only; the header has the time


@tid("DATA-02")
async def test_DATA_02_export_restores_into_a_fresh_database_identically(
    api: ApiFactory, engine: AsyncEngine, database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, _, p = await kitchen_world(api)  # an open bill, payments, kitchen, asks: most tables
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit", "note": "curry"})
    await p["arjun"].post(
        f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("arjun"), "amount_cents": 3391, "method": "zelle"}
    )
    await w.admin.post("/__test/drain", idem=False)
    async with engine.connect() as conn, conn.begin():
        data, meta = await backup.export(conn, NOW)
    assert meta["tables"]["bills"] == 1 and meta["tables"]["house_events"] > 10
    assert meta["sha256"] and meta["bytes"] == len(data)

    url = await _fresh_database(engine, database_url)
    target = make_engine(url, pool_size=2)
    try:
        async with target.begin() as conn:
            counts = await backup.restore(conn, data)
        assert counts == {k: v for k, v in meta["tables"].items()}
        async with target.connect() as conn, conn.begin():
            again, _ = await backup.export(conn, NOW)
        assert _body(again) == _body(data), "every row of every table comes back identical"
        async with make_sessionmaker(target)() as db:
            assert await check_all(db, NOW) == [], "the restored database passes the invariant checks"
        with pytest.raises(ValueError, match="not empty"):  # never restore over existing data
            async with target.begin() as conn:
                await backup.restore(conn, data)
    finally:
        await target.dispose()


@tid("DATA-02")
async def test_DATA_02_export_refuses_a_different_revision(engine: AsyncEngine) -> None:
    async with engine.connect() as conn, conn.begin():
        data, _ = await backup.export(conn, NOW)
    lines = gzip.decompress(data).decode().splitlines()
    lines[0] = lines[0].replace('"alembic_revision": "', '"alembic_revision": "x')
    forged = gzip.compress("\n".join(lines).encode())
    with pytest.raises(ValueError, match="migrate first"):
        async with engine.connect() as conn, conn.begin():
            await backup.restore(conn, forged)


@tid("DATA-05")
async def test_DATA_05_invariants_clean_on_seeded_and_busy_data(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from tests.test_tabs_and_account import apt4b

    await apt4b(api, sessions)
    w, bill_id, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    ask = await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
    await w.admin.post(f"/api/v1/requests/{ask.json()['id']}/answer", {"decision": "split"})
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    await w.admin.post("/__test/drain", idem=False)
    async with sessions() as db:
        assert await check_all(db, datetime.now(UTC)) == []


@tid("DATA-05")
async def test_DATA_05_invariant_checker_catches_corruption(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from sqlalchemy import update

    from app.models import HouseEvent, InventoryOwner, ShareSnapshot

    await kitchen_world(api)
    async with sessions() as db, db.begin():
        snap = (await db.scalars(select(ShareSnapshot).order_by(ShareSnapshot.computed_at.desc()).limit(1))).one()
        snap.share_cents += 1  # a share that no longer adds up
        owner = (await db.scalars(select(InventoryOwner).limit(1))).one()
        await db.delete(owner)  # an owner missing from an allocated line
        await db.execute(update(HouseEvent).where(HouseEvent.seq == 3).values(seq=999))  # a gap
    async with sessions() as db:
        found = await check_all(db, datetime.now(UTC))
    assert any("stored shares" in v for v in found)
    assert any("owners" in v and "allocated" in v for v in found)
    assert any("gaps" in v for v in found)
