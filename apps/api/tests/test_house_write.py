"""The house write helper (CLAUDE.md rule 3, PRD §21.2): gapless sequence in commit order,
nothing published for a rolled-back write, events/activity/outbox written in the same commit."""

from __future__ import annotations

import functools
import os
import time
import uuid
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import ControllableClock
from app.models import ActivityEvent, House, HouseEvent, OutboxRow
from app.services.bus import NullBus
from app.services.house_write import Activity, WriteContext, house_write
from tests.factories import make_house
from tests.harness.race import run_concurrently, sync_iterations
from tests.harness.recorder import EventRecorder
from tests.harness.tags import tid


async def _touch(writes: WriteContext, house_id: uuid.UUID, i: int, *, fail: bool = False) -> None:
    async with house_write(writes, house_id, client_mutation_id=f"m-{i}") as w:
        await w.session.execute(text("SELECT pg_sleep(0.001)"))
        w.emit(
            "test_touched",
            aggregate=("house", house_id, None),
            data={"i": i},
            activity=Activity("test_touched", payload={"i": i}),
            notify=["share"] if i % 2 else [],
        )
        if fail:
            raise RuntimeError("boom")


async def _event_count(sessions: async_sessionmaker[AsyncSession], house_id: uuid.UUID) -> int:
    async with sessions() as s:
        return int(
            await s.scalar(
                select(func.count()).select_from(HouseEvent).where(HouseEvent.house_id == house_id)
            )
            or 0
        )


@pytest.mark.sync
@tid("SYNC-25")
async def test_SYNC_25_gapless_sequence_equals_commit_order(
    writes: WriteContext, sessions: async_sessionmaker[AsyncSession], redis: Redis, clock: ControllableClock
) -> None:
    for _ in range(sync_iterations()):
        house, _members = await make_house(sessions, clock.now())
        async with EventRecorder(redis, house.id) as rec:
            touch = functools.partial(_touch, writes, house.id)
            outcomes = await run_concurrently(50, touch)
            assert all(o.ok for o in outcomes), [o.error for o in outcomes if not o.ok]
            await rec.wait_for(50)
            rec.assert_gapless()

        async with sessions() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT seq, pg_xact_commit_timestamp(xmin) AS committed FROM house_events "
                        "WHERE house_id = :h ORDER BY seq"
                    ),
                    {"h": house.id},
                )
            ).all()
            seq_after = await s.scalar(select(House.event_seq).where(House.id == house.id))
        assert [r.seq for r in rows] == list(range(1, 51))
        assert seq_after == 50
        committed = [r.committed for r in rows]
        assert None not in committed, "track_commit_timestamp must be on for this test"
        assert committed == sorted(committed), "sequence order differs from commit order"


@tid("SYNC-26")
async def test_SYNC_26_rolled_back_write_publishes_nothing(
    writes: WriteContext, sessions: async_sessionmaker[AsyncSession], redis: Redis, clock: ControllableClock
) -> None:
    house, _ = await make_house(sessions, clock.now())
    async with EventRecorder(redis, house.id) as rec:
        with pytest.raises(RuntimeError):
            await _touch(writes, house.id, 1, fail=True)
        await _touch(writes, house.id, 2)
        await rec.wait_for(1)
        await rec.settle()
    assert [e["data"]["i"] for e in rec.events] == [2]
    assert rec.seqs == [1], "the rolled-back write must not consume a sequence number"
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(ActivityEvent)) == 1
        assert await s.scalar(select(func.count()).select_from(OutboxRow)) == 0  # i=2 notifies nobody


async def test_noop_write_takes_no_number(
    writes: WriteContext, sessions: async_sessionmaker[AsyncSession], clock: ControllableClock
) -> None:
    house, _ = await make_house(sessions, clock.now())
    async with house_write(writes, house.id):
        pass  # e.g. SYNC-02: second identical set-state write changes nothing
    assert await _event_count(sessions, house.id) == 0


async def test_event_activity_and_outbox_share_one_commit(
    writes: WriteContext, sessions: async_sessionmaker[AsyncSession], clock: ControllableClock
) -> None:
    house, members = await make_house(sessions, clock.now())
    async with house_write(writes, house.id, actor_member_id=members[0].id, client_mutation_id="cm-1") as w:
        w.emit("a", aggregate=("house", house.id, 1), activity=Activity("a"), notify=["tab", "share"])
        w.emit("b", aggregate=("house", house.id, 1))
    async with sessions() as s:
        events = (await s.scalars(select(HouseEvent).order_by(HouseEvent.seq))).all()
        activity = (await s.scalars(select(ActivityEvent))).all()
        outbox = (await s.scalars(select(OutboxRow).order_by(OutboxRow.kind))).all()
    assert [(e.seq, e.type, e.client_mutation_id, e.actor_member_id) for e in events] == [
        (1, "a", "cm-1", members[0].id),
        (2, "b", "cm-1", members[0].id),
    ]
    assert [(a.type, a.house_event_seq) for a in activity] == [("a", 1)]
    assert [(o.kind, o.seq, o.status) for o in outbox] == [("share", 1, "pending"), ("tab", 1, "pending")]


async def test_touch_house_bumps_version_with_the_sequence(
    writes: WriteContext, sessions: async_sessionmaker[AsyncSession], clock: ControllableClock
) -> None:
    house, _ = await make_house(sessions, clock.now())
    async with house_write(writes, house.id) as w:
        w.touch_house()
        w.emit("house_renamed", aggregate=("house", house.id, 2))
    async with sessions() as s:
        row = (await s.execute(select(House.version, House.event_seq).where(House.id == house.id))).one()
    assert (row.version, row.event_seq) == (2, 1)


class _BrokenBus:
    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        raise ConnectionError("redis down")


async def test_lost_publish_does_not_fail_the_write(
    sessions: async_sessionmaker[AsyncSession], clock: ControllableClock
) -> None:
    """SYNC-18 precondition: a failed publish is logged, the commit stands, the next event shows the gap."""
    house, _ = await make_house(sessions, clock.now())
    await _touch(WriteContext(sessions, clock, _BrokenBus()), house.id, 1)
    await _touch(WriteContext(sessions, clock, NullBus()), house.id, 2)
    assert await _event_count(sessions, house.id) == 2


@pytest.mark.sync
async def test_write_helper_load_twenty_writers(
    writes: WriteContext, sessions: async_sessionmaker[AsyncSession], clock: ControllableClock
) -> None:
    """Early load check of the write path ahead of SYNC-24 (agreed risk 25): 20 writers on one house,
    each locking an aggregate row first and the house last, as fast as possible. Deliberately not
    tagged SYNC-24: the real test (tick endpoint, share invariant, devices converging) lands in
    Milestone 6. Runs 3 s under `make test`, 30 s under `make test-sync`."""
    duration = float(os.environ.get("SYNC_24_SECONDS", "3"))
    house, _ = await make_house(sessions, clock.now())
    latencies: list[float] = []
    deadline = time.perf_counter() + duration

    async def writer(i: int) -> int:
        done = 0
        while time.perf_counter() < deadline:
            started = time.perf_counter()
            async with house_write(writes, house.id, client_mutation_id=f"w{i}-{done}") as w:
                # Aggregate lock first (a member row stands in for a bill), house lock last.
                await w.session.execute(
                    text("SELECT id FROM members WHERE house_id = :h ORDER BY sort_order LIMIT 1 FOR UPDATE"),
                    {"h": house.id},
                )
                w.emit("tick", aggregate=("house", house.id, None), activity=Activity("tick"))
            latencies.append(time.perf_counter() - started)
            done += 1
        return done

    outcomes = await run_concurrently(20, writer, timeout=duration + 60)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes if not o.ok]
    total = sum(o.value or 0 for o in outcomes)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    print(f"write helper load: {total} writes in {duration:.0f}s, p95 {p95 * 1000:.1f} ms")
    assert await _event_count(sessions, house.id) == total
    async with sessions() as s:
        seqs = (
            await s.scalars(
                select(HouseEvent.seq).where(HouseEvent.house_id == house.id).order_by(HouseEvent.seq)
            )
        ).all()
    assert list(seqs) == list(range(1, total + 1))
    assert p95 < 0.300, f"p95 {p95 * 1000:.0f} ms exceeds 300 ms"
