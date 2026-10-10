"""SYNC-24 (PRD §21.7): 20 people tick across one bill as fast as possible for 30 s.

The API runs as 4 worker processes (tests/harness/multiserver.py), like production, so client
latency reflects the server rather than a test process shared with 20 clients.

No lost updates, Σ shares = total after every commit, p95 write < 300 ms, every device converges.
`SYNC_24_SECONDS` sets the duration (3 s in `make test`, 30 s in `make test-sync`).
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.models import HouseEvent
from app.services.invariants import check_all
from tests.harness.api import Api
from tests.harness.multiserver import multi_process_server
from tests.harness.tags import fr, tid
from tests.test_bills import World, draft_with_items, tick_like_fixture
from tests.test_sync_api import tick

ApiFactory = Callable[[], Api]
NAMES = [f"Mate{n:02d}" for n in range(1, 20)]  # with the admin: 20 people, the house maximum


@pytest.mark.sync
@tid("SYNC-24")
@fr("FR-32", "FR-62")
async def test_SYNC_24_twenty_people_tick_one_bill_for_thirty_seconds(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with multi_process_server(workers=int(os.environ.get("SYNC_24_WORKERS", "4"))) as server:
        await _twenty_people(server.api, sessions)


async def _twenty_people(api: ApiFactory, sessions: async_sessionmaker[AsyncSession]) -> None:
    admin = api()
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    w = World(admin, await admin.create_house(roommates=["Arjun", "Meera", "Kabir", *NAMES[3:]]))
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    assert (await admin.post(f"/api/v1/bills/{bill_id}/send")).status_code == 200
    people: dict[str, Api] = {"pranav": admin}
    for name in [n for n in w.member if n != "pranav"]:
        who = api()
        await who.sign_in(f"{name}+{uuid7()}@example.com")
        r = await who.post(f"/api/v1/join/{w.house['invite_token']}/claim", {"member_id": w.member[name]})
        assert r.status_code == 200, r.text
        people[name] = who
    assert len(people) == 20
    total = (await admin.get(f"/api/v1/bills/{bill_id}")).json()["bill"]["total_cents"]

    duration = float(os.environ.get("SYNC_24_SECONDS", "3"))
    latencies: list[float] = []
    last_ack: dict[tuple[str, str], bool] = {}  # (person, item) -> the state the server confirmed last
    failures: list[str] = []
    deadline = time.monotonic() + duration

    async def roommate(name: str, seed: int) -> None:
        rng = random.Random(seed)
        who = people[name]
        while time.monotonic() < deadline:
            item = rng.choice(ids)
            on = rng.random() < 0.6
            started = time.perf_counter()
            r = await tick(who, bill_id, item, w.key(name), on)
            latencies.append(time.perf_counter() - started)
            if r.status_code == 200:
                last_ack[(name, item)] = on
            elif r.status_code == 409 and r.json()["code"] == "LAST_PERSON":
                pass  # someone has to keep the item: refused, state unchanged
            else:
                failures.append(f"{name}: {r.status_code} {r.text[:200]}")

    await asyncio.gather(*(roommate(n, i) for i, n in enumerate(people)))
    assert failures == []
    assert {name for name, _ in last_ack} == set(people), "nobody was starved"

    # No lost updates: each cell ends as its owner last saw it confirmed.
    view = (await admin.get(f"/api/v1/bills/{bill_id}")).json()
    on_server = {
        (name, item["id"])
        for item in view["items"]
        for a in item["allocations"]
        for name in people
        if a["participant"] == w.key(name)
    }
    for (name, item), on in last_ack.items():
        assert ((name, item) in on_server) == on, f"lost update: {name} on {item}"

    # Σ shares = total after every commit (each allocation event carries the shares it committed).
    async with sessions() as db:
        events = (
            await db.scalars(
                select(HouseEvent).where(HouseEvent.house_id == w.hid, HouseEvent.type == "allocation_changed")
            )
        ).all()
    committed: list[dict[str, Any]] = [e.data for e in events if e.data and e.data.get("shares")]
    assert committed and all(sum(d["shares"].values()) == total for d in committed)
    assert sum(view["shares"].values()) == total

    # The stored share snapshots, kitchen owners and event log agree with the allocations.
    async with sessions() as db:
        assert await check_all(db, datetime.now(UTC)) == []

    # Every device converges on the same bill.
    views = await asyncio.gather(*(p.get(f"/api/v1/bills/{bill_id}") for p in people.values()))
    shapes = {
        repr((v.json()["shares"], [sorted(a["participant"] for a in i["allocations"]) for i in v.json()["items"]]))
        for v in views
    }
    assert len(shapes) == 1

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    assert p95 < 0.300, f"p95 write {p95 * 1000:.0f} ms over {len(latencies)} writes"
