"""House event stream over real HTTP (PRD §21.5; ADR-0012): replay, gaps, resync, revoked,
rollover at 270 s, restarts, and the Postgres-only (PROFILE=vercel) path."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.models import HouseEvent
from app.settings import Settings
from tests.harness.api import Api
from tests.harness.live import LiveServer, SseStream, live_server
from tests.harness.race import run_concurrently
from tests.harness.tags import fr, tid
from tests.test_bills import World, draft_with_items, tick_like_fixture


@pytest.fixture
async def server(settings: Settings, db_reset: None) -> AsyncIterator[LiveServer]:
    async with live_server(settings) as s:
        yield s


@pytest.fixture
async def vercel_server(settings: Settings, db_reset: None) -> AsyncIterator[LiveServer]:
    """PROFILE=vercel: no Redis at all; streams are fed by polling Postgres only (VERCEL-02)."""
    async with live_server(settings.model_copy(update={"profile": "vercel", "redis_url": None})) as s:
        yield s


async def setup(server: LiveServer) -> tuple[World, str, list[str], Api]:
    admin = Api(server.client())
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    w = World(admin, await admin.create_house())
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    await admin.post(f"/api/v1/bills/{bill_id}/send")
    meera = Api(server.client())
    await meera.sign_in(f"meera+{uuid7()}@example.com")
    await meera.post(f"/api/v1/join/{w.house['invite_token']}/claim", {"member_id": w.member["meera"]})
    return w, bill_id, ids, meera


async def flip(w: World, bill_id: str, item_id: str, on: bool) -> None:
    r = await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": item_id, "participant": w.key("kabir"), "on": on}]},
    )
    assert r.status_code == 200, r.text


@tid("SYNC-17")
@fr("FR-32")
async def test_SYNC_17_reconnect_replays_missed_events_in_order(server: LiveServer) -> None:
    w, bill_id, ids, meera = await setup(server)
    async with SseStream(meera.http, w.hid) as live:
        first = (await live.wait_event("ready")).data["seq"]
    for on in (True, False, True):  # three changes while disconnected
        await flip(w, bill_id, ids[3], on)
    async with SseStream(meera.http, w.hid, last_event_id=first) as resumed:
        events = await resumed.wait_for(3)
        assert resumed.seqs == [first + 1, first + 2, first + 3]
    assert [e["type"] for e in events] == ["allocation_changed"] * 3
    fresh = (await meera.get(f"/api/v1/bills/{bill_id}")).json()
    assert events[-1]["data"]["shares"] == fresh["shares"], "the screen equals a fresh GET"


@tid("SYNC-18", "CHAOS-02")
async def test_SYNC_18_lost_publish_is_filled_by_the_gap_fetch(
    server: LiveServer, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, meera = await setup(server)
    async with sessions() as db:
        start = await db.scalar(text("SELECT event_seq FROM houses WHERE id = :h"), {"h": w.hid})
    await flip(w, bill_id, ids[3], True)
    await flip(w, bill_id, ids[3], False)
    # A client that saw only the second event (the first publish "was lost") fills the gap:
    gap = (await meera.get(f"/api/v1/houses/{w.hid}/events", params={"after": start})).json()
    assert gap["resync"] is False and [e["seq"] for e in gap["events"]] == [start + 1, start + 2]


@tid("SYNC-19")
async def test_SYNC_19_tab_left_open_for_eight_days_gets_resync(
    server: LiveServer, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, meera = await setup(server)
    async with SseStream(meera.http, w.hid) as s:
        seen = (await s.wait_event("ready")).data["seq"]
    await flip(w, bill_id, ids[3], True)
    async with sessions() as db, db.begin():  # the events after `seen` are now 8 days old
        await db.execute(
            update(HouseEvent)
            .where(HouseEvent.house_id == w.hid, HouseEvent.seq > seen)
            .values(created_at=HouseEvent.created_at - timedelta(days=8))
        )
    async with SseStream(meera.http, w.hid, last_event_id=seen) as stale:
        resync = await stale.wait_event("resync")
    assert resync.data["seq"] > seen
    assert (await meera.get(f"/api/v1/houses/{w.hid}/events", params={"after": seen})).json()["resync"] is True


@tid("SYNC-27")
async def test_SYNC_27_more_than_500_events_behind_gets_resync(
    server: LiveServer, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, meera = await setup(server)
    async with sessions() as db, db.begin():
        top = await db.scalar(text("SELECT event_seq FROM houses WHERE id = :h"), {"h": w.hid})
        rows = [
            {
                "house_id": w.hid,
                "seq": top + i,
                "type": "test_bulk",
                "aggregate_type": "house",
                "aggregate_id": w.hid,
                "created_at": datetime.now(UTC),
            }
            for i in range(1, 502)
        ]
        await db.execute(insert(HouseEvent), rows)
        await db.execute(text("UPDATE houses SET event_seq = :s WHERE id = :h"), {"s": top + 501, "h": w.hid})
    async with SseStream(meera.http, w.hid, last_event_id=top) as s:
        assert (await s.wait_event("resync")).data["seq"] == top + 501


@tid("SYNC-21")
async def test_SYNC_21_api_restart_resumes_without_missing_events(settings: Settings, db_reset: None) -> None:
    first = LiveServer(settings)
    await asyncio.to_thread(first.start)
    w, bill_id, ids, meera = await setup(first)
    async with SseStream(meera.http, w.hid) as s:
        seen = (await s.wait_event("ready")).data["seq"]
    await asyncio.to_thread(first.stop)  # deploy: the process goes away
    second = LiveServer(settings, port=first.port)
    await asyncio.to_thread(second.start)
    try:
        await flip(w, bill_id, ids[3], True)
        async with SseStream(meera.http, w.hid, last_event_id=seen) as resumed:
            await resumed.wait_for(1)
            assert resumed.seqs == [seen + 1]
    finally:
        await asyncio.to_thread(second.stop)


@tid("SYNC-33", "SYNC-12")
@fr("FR-06")
async def test_SYNC_33_removed_members_stream_is_revoked_within_five_minutes(server: LiveServer) -> None:
    w, _, _, meera = await setup(server)
    async with SseStream(meera.http, w.hid) as s:
        await s.wait_event("ready")
        assert (await w.admin.call("DELETE", f"/api/v1/houses/{w.hid}/members/{w.member['meera']}")).status_code == 200
        await w.admin.post("/__test/clock", {"advance_seconds": 301}, idem=False)
        revoked = await s.wait_event("revoked", timeout=5)
    assert revoked.data == {"reason": "not_a_member"}
    await asyncio.wait_for(s.closed.wait(), 5)


@tid("VERCEL-01")
async def test_VERCEL_01_stream_rolls_over_and_reconnect_loses_nothing(settings: Settings, db_reset: None) -> None:
    """The stream closes cleanly at its max duration (270 s in production; 2 s here) and the
    reconnect with Last-Event-ID delivers everything exactly once. `make launch-check` runs the
    real 270 s rollover against the local-vercel rehearsal."""
    async with live_server(settings, sse_max_seconds=2.0) as server:
        w, bill_id, ids, meera = await setup(server)
        got: list[int] = []
        last: int | None = None
        deadline = time.monotonic() + 12
        writes = 0
        while time.monotonic() < deadline and writes < 6:
            async with SseStream(meera.http, w.hid, last_event_id=last) as s:
                if last is None:
                    last = (await s.wait_event("ready")).data["seq"]
                await flip(w, bill_id, ids[3], writes % 2 == 0)
                writes += 1
                rolled = await s.wait_event("rollover", timeout=6)
                got += s.seqs
                last = rolled.data["seq"]
        assert writes >= 3
        assert got == sorted(set(got)), "no duplicates, in order"
        assert got == list(range(got[0], got[0] + writes))


@tid("VERCEL-02")
@fr("FR-32")
async def test_VERCEL_02_postgres_only_delivery_within_two_seconds(vercel_server: LiveServer) -> None:
    w, bill_id, ids, meera = await setup(vercel_server)
    async with SseStream(meera.http, w.hid) as s:
        await s.wait_event("ready")
        started = time.monotonic()
        await flip(w, bill_id, ids[3], True)
        await s.wait_for(1, timeout=2.0)
        assert time.monotonic() - started < 2.0
        assert s.messages[0]["type"] == "allocation_changed"


@tid("SYNC-25")
async def test_SYNC_25_sse_delivers_gapless_in_commit_order(server: LiveServer) -> None:
    """Over SSE the stream is ordered: 30 concurrent writes arrive as seq 1..N in order."""
    w, bill_id, ids, meera = await setup(server)
    async with SseStream(meera.http, w.hid) as s:
        base = (await s.wait_event("ready")).data["seq"]

        async def write(i: int) -> Any:
            return await w.admin.call(
                "PUT",
                f"/api/v1/bills/{bill_id}/allocations",
                {"changes": [{"item_id": ids[i % 12], "participant": w.key("kabir"), "on": i % 2 == 0}]},
            )

        await run_concurrently(30, write)
        await asyncio.sleep(1.5)
        assert s.seqs == list(range(base + 1, base + 1 + len(s.seqs)))
