"""The one helper every house write goes through (CLAUDE.md rule 3, PRD §21.2).

    async with house_write(ctx, house_id, actor_member_id=..., client_mutation_id=...) as w:
        ...reads, checks and writes on w.session...
        w.emit("allocation_changed", aggregate=("bill", bill.id, bill.version), data={...},
               activity=Activity("tick", bill_id=bill.id, payload={...}), notify=["share"])

On a clean exit, and only then, the helper:
  1. takes the next number(s) from `houses.event_seq` with UPDATE ... RETURNING. This is the
     *last* statement and the only place a write locks the house row ("house lock last"), so
     sequence order equals commit order and there are no gaps;
  2. inserts one `house_events` row per emitted event, the matching `activity_events` rows and any
     `outbox` rows;
  3. commits, then publishes each event to Redis `house:{id}`. A failed publish is logged and
     ignored (clients detect the gap).

An exception anywhere rolls the transaction back and nothing is published (SYNC-26). A write
that emits nothing (an idempotent no-op) takes no number and publishes nothing.

Lock order for every write: aggregate rows first (`SELECT ... FOR UPDATE` on bills, items,
requests ...), house row last, via this helper. Never lock `houses` earlier in a write; a guarded
house edit (rename) updates the row through `w.touch_house()` which defers it to step 1.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.models import ActivityEvent, HouseEvent, OutboxRow
from app.services.bus import EventBus, house_channel, publish_safely


@dataclass(frozen=True)
class Activity:
    """An `activity_events` row written with the event (FR-43, DATA-06)."""

    type: str
    bill_id: uuid.UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    actor_user_id: uuid.UUID | None = None


@dataclass(frozen=True)
class PendingEvent:
    type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    version: int | None
    data: dict[str, Any] | None
    activity: Activity | None
    notify: tuple[str, ...]


@dataclass(frozen=True)
class CommittedEvent:
    house_id: uuid.UUID
    seq: int
    type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    version: int | None
    actor_member_id: uuid.UUID | None
    client_mutation_id: str | None
    data: dict[str, Any] | None

    def wire(self) -> dict[str, Any]:
        """The SSE/Redis payload shape from PRD §21.5."""
        return {
            "seq": self.seq,
            "type": self.type,
            "aggregate": {"type": self.aggregate_type, "id": str(self.aggregate_id), "version": self.version},
            "actor_member_id": str(self.actor_member_id) if self.actor_member_id else None,
            "client_mutation_id": self.client_mutation_id,
            "data": self.data,
        }


@dataclass
class WriteContext:
    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    bus: EventBus


class HouseWrite:
    def __init__(
        self,
        session: AsyncSession,
        house_id: uuid.UUID,
        actor_member_id: uuid.UUID | None,
        client_mutation_id: str | None,
    ) -> None:
        self.session = session
        self.house_id = house_id
        self.actor_member_id = actor_member_id
        self.client_mutation_id = client_mutation_id
        self._pending: list[PendingEvent] = []
        self._bump_house_version = False
        self.committed: list[CommittedEvent] = []

    def emit(
        self,
        type: str,
        *,
        aggregate: tuple[str, uuid.UUID, int | None],
        data: dict[str, Any] | None = None,
        activity: Activity | None = None,
        notify: Sequence[str] = (),
    ) -> None:
        agg_type, agg_id, version = aggregate
        self._pending.append(PendingEvent(type, agg_type, agg_id, version, data, activity, tuple(notify)))

    def touch_house(self) -> None:
        """Bump `houses.version` (guarded house edits) together with the sequence, house lock last."""
        self._bump_house_version = True

    async def _finish(self, now_clock: Clock) -> None:
        if not self._pending:
            return
        n = len(self._pending)
        version_sql = ", version = version + 1" if self._bump_house_version else ""
        row = (
            await self.session.execute(
                text(f"UPDATE houses SET event_seq = event_seq + :n{version_sql} WHERE id = :id RETURNING event_seq"),
                {"n": n, "id": self.house_id},
            )
        ).first()
        if row is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "House not found")
        first = int(row[0]) - n + 1
        now = now_clock.now()
        events, activities, outbox = [], [], []
        for offset, ev in enumerate(self._pending):
            seq = first + offset
            committed = CommittedEvent(
                self.house_id, seq, ev.type, ev.aggregate_type, ev.aggregate_id, ev.version,
                self.actor_member_id, self.client_mutation_id, ev.data,
            )
            self.committed.append(committed)
            events.append({
                "house_id": self.house_id, "seq": seq, "type": ev.type, "aggregate_type": ev.aggregate_type,
                "aggregate_id": ev.aggregate_id, "version": ev.version, "actor_member_id": self.actor_member_id,
                "client_mutation_id": self.client_mutation_id, "data": ev.data, "created_at": now,
            })
            if ev.activity is not None:
                activities.append({
                    "id": uuid7(), "house_id": self.house_id, "bill_id": ev.activity.bill_id,
                    "actor_user_id": ev.activity.actor_user_id, "actor_member_id": self.actor_member_id,
                    "type": ev.activity.type, "payload": ev.activity.payload, "house_event_seq": seq,
                    "created_at": now,
                })
            outbox += [
                {"id": uuid7(), "house_id": self.house_id, "seq": seq, "kind": kind, "status": "pending",
                 "attempts": 0, "next_attempt_at": now, "created_at": now}
                for kind in ev.notify
            ]
        await self.session.execute(insert(HouseEvent), events)
        if activities:
            await self.session.execute(insert(ActivityEvent), activities)
        if outbox:
            await self.session.execute(insert(OutboxRow), outbox)


@asynccontextmanager
async def house_write(
    ctx: WriteContext,
    house_id: uuid.UUID,
    *,
    actor_member_id: uuid.UUID | None = None,
    client_mutation_id: str | None = None,
) -> AsyncIterator[HouseWrite]:
    async with ctx.sessions() as session:
        async with session.begin():
            write = HouseWrite(session, house_id, actor_member_id, client_mutation_id)
            yield write
            await write._finish(ctx.clock)
        # Committed. Publish strictly after commit (§21.5).
    for event in write.committed:
        await publish_safely(ctx.bus, house_channel(house_id), event.wire())
