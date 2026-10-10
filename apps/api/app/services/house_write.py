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

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.queue import Kicker, kick_safely
from app.clock import Clock
from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.services.bus import EventBus, house_channel, publish_safely

IDEMPOTENCY_TTL = timedelta(hours=24)


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


@dataclass(frozen=True)
class Idempotency:
    """`Idempotency-Key` for a create or transition (PRD §21.3); stored with the write itself."""

    user_id: uuid.UUID
    key: str
    request_hash: str


class IdempotentReplay(Exception):
    """The same key already completed: return the stored response instead of writing again."""

    def __init__(self, status_code: int, body: Any) -> None:
        super().__init__("idempotent replay")
        self.status_code = status_code
        self.body = body


@dataclass
class WriteContext:
    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    bus: EventBus
    kicker: Kicker | None = None  # wakes the outbox drain after commit (ADR-0013)


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
        self.idempotency: Idempotency | None = None
        self._response: tuple[int, Any] | None = None
        self.outbox_rows = 0
        self._locked_seq: int | None = None

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
        if activity is None:
            # DATA-06: every state change has exactly one activity entry. Changes that aren't part of
            # a sent bill's story (draft ticks, kitchen switches) get a house-level entry with no
            # bill, so bill activity feeds stay as they were.
            activity = Activity(type)
        self._pending.append(PendingEvent(type, agg_type, agg_id, version, data, activity, tuple(notify)))

    def respond(self, status_code: int, body: Any) -> None:
        """The response a retry with the same Idempotency-Key gets back (stored in this commit)."""
        self._response = (status_code, body)

    async def _claim_idempotency_key(self, now: Any) -> None:
        """First statement of the transaction. A concurrent request with the same key blocks on the
        primary key until this transaction ends, then replays what it committed."""
        idem = self.idempotency
        if idem is None:
            return
        inserted = (
            await self.session.execute(
                text(
                    "INSERT INTO idempotency_keys (user_id, key, request_hash, created_at) "
                    "VALUES (:u, :k, :h, :t) ON CONFLICT (user_id, key) DO NOTHING RETURNING key"
                ),
                {"u": idem.user_id, "k": idem.key, "h": idem.request_hash, "t": now},
            )
        ).first()
        if inserted is not None:
            return
        row = (
            await self.session.execute(
                text(
                    "SELECT request_hash, status_code, response_body, created_at FROM idempotency_keys "
                    "WHERE user_id = :u AND key = :k FOR NO KEY UPDATE"
                ),
                {"u": idem.user_id, "k": idem.key},
            )
        ).one()
        if row.created_at < now - IDEMPOTENCY_TTL:
            # Replays last 24 h (PRD §21.8); an older key is a new request.
            await self.session.execute(
                text(
                    "UPDATE idempotency_keys SET request_hash = :h, status_code = NULL, response_body = NULL, "
                    "created_at = :t WHERE user_id = :u AND key = :k"
                ),
                {"u": idem.user_id, "k": idem.key, "h": idem.request_hash, "t": now},
            )
            return
        if row.request_hash != idem.request_hash:
            raise Problem(422, ErrorCode.IDEMPOTENCY_MISMATCH, "This Idempotency-Key was used for a different request")
        raise IdempotentReplay(int(row.status_code or 200), row.response_body)

    async def lock_house(self) -> None:
        """Take the house lock now instead of at commit. It stays the LAST lock this write takes
        (row locks come first), and every read after it sees all writes that committed before:
        concurrent ticks use it so each computes shares from the latest allocations (SYNC-24)."""
        row = (
            await self.session.execute(
                text("SELECT event_seq FROM houses WHERE id = :h FOR NO KEY UPDATE"), {"h": self.house_id}
            )
        ).first()
        if row is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "House not found")
        self._locked_seq = int(row[0])  # nobody else can take a number until we commit

    def touch_house(self) -> None:
        """Bump `houses.version` (guarded house edits) together with the sequence, house lock last."""
        self._bump_house_version = True

    async def _finish(self, now_clock: Clock) -> None:
        if self.idempotency is not None:
            if self._response is None:
                raise RuntimeError("an idempotent write must call respond() before it ends")
            await self.session.execute(
                text(
                    "UPDATE idempotency_keys SET status_code = :s, response_body = CAST(:b AS jsonb) "
                    "WHERE user_id = :u AND key = :k"
                ),
                {
                    "s": self._response[0],
                    "b": json.dumps(self._response[1], default=str),
                    "u": self.idempotency.user_id,
                    "k": self.idempotency.key,
                },
            )
        if not self._pending:
            return
        n = len(self._pending)
        bump_version = "version = version + 1, " if self._bump_house_version else ""
        if self._locked_seq is not None:
            # The lock was taken early and we already know the last number: moving the sequence
            # travels in the same statement as the rows below (one round trip).
            first = self._locked_seq + 1
        else:
            sql = f"UPDATE houses SET {bump_version}event_seq = event_seq + :n WHERE id = :id RETURNING event_seq"  # noqa: S608 - constant fragments only
            row = (await self.session.execute(text(sql), {"n": n, "id": self.house_id})).first()
            if row is None:
                raise Problem(404, ErrorCode.NOT_FOUND, "House not found")
            first = int(row[0]) - n + 1
        now = now_clock.now()
        events, activities, outbox = [], [], []
        for offset, ev in enumerate(self._pending):
            seq = first + offset
            committed = CommittedEvent(
                self.house_id,
                seq,
                ev.type,
                ev.aggregate_type,
                ev.aggregate_id,
                ev.version,
                self.actor_member_id,
                self.client_mutation_id,
                ev.data,
            )
            self.committed.append(committed)
            events.append(
                {
                    "house_id": self.house_id,
                    "seq": seq,
                    "type": ev.type,
                    "aggregate_type": ev.aggregate_type,
                    "aggregate_id": ev.aggregate_id,
                    "version": ev.version,
                    "actor_member_id": self.actor_member_id,
                    "client_mutation_id": self.client_mutation_id,
                    "data": ev.data,
                    "created_at": now,
                }
            )
            if ev.activity is not None:
                activities.append(
                    {
                        "id": uuid7(),
                        "house_id": self.house_id,
                        "bill_id": ev.activity.bill_id,
                        "actor_user_id": ev.activity.actor_user_id,
                        "actor_member_id": self.actor_member_id,
                        "type": ev.activity.type,
                        "payload": ev.activity.payload,
                        "house_event_seq": seq,
                        "created_at": now,
                    }
                )
            outbox += [
                {
                    "id": uuid7(),
                    "house_id": self.house_id,
                    "seq": seq,
                    "kind": kind,
                    "status": "pending",
                    "attempts": 0,
                    "next_attempt_at": now,
                    "created_at": now,
                }
                for kind in ev.notify
            ]
        # One statement, one round trip, prepared once: data-modifying CTEs insert the events, their
        # activity entries and outbox rows from arrays (and move the sequence when the lock was
        # taken early). Foreign keys are checked at the end of the statement, so children may
        # reference events inserted alongside them.
        locked = self._locked_seq is not None
        sql = _FINISH_SQL[(locked, locked and self._bump_house_version)]
        params: dict[str, Any] = {
            "house": self.house_id,
            "actor": self.actor_member_id,
            "cmid": self.client_mutation_id,
            "now": now,
            "last": first + n - 1,
            "ev_seq": [e["seq"] for e in events],
            "ev_type": [e["type"] for e in events],
            "ev_agg": [e["aggregate_type"] for e in events],
            "ev_aid": [e["aggregate_id"] for e in events],
            "ev_ver": [e["version"] for e in events],
            "ev_data": [None if e["data"] is None else json.dumps(e["data"], default=str) for e in events],
            "ac_id": [a["id"] for a in activities],
            "ac_bill": [a["bill_id"] for a in activities],
            "ac_user": [a["actor_user_id"] for a in activities],
            "ac_type": [a["type"] for a in activities],
            "ac_payload": [json.dumps(a["payload"], default=str) for a in activities],
            "ac_seq": [a["house_event_seq"] for a in activities],
            "ob_id": [o["id"] for o in outbox],
            "ob_seq": [o["seq"] for o in outbox],
            "ob_kind": [o["kind"] for o in outbox],
        }
        await self.session.execute(text(sql), params)
        self.outbox_rows = len(outbox)


def _finish_sql(locked: bool, bump_version: bool) -> str:
    """Built once at import from constant fragments; every value is a bound parameter."""
    update = (
        ", hs AS (UPDATE houses SET "  # noqa: S608 - constant fragments; values are bound
        + ("version = version + 1, " if bump_version else "")
        + "event_seq = :last WHERE id = :house RETURNING 1)"
        if locked
        else ""
    )
    return (
        "WITH ev AS ("  # noqa: S608
        "INSERT INTO house_events (house_id, seq, type, aggregate_type, aggregate_id, version, actor_member_id, "
        "client_mutation_id, data, created_at) "
        "SELECT :house, x.seq, x.type, x.agg, x.aid, x.ver, :actor, :cmid, CAST(x.data AS jsonb), :now "
        "FROM unnest(CAST(:ev_seq AS bigint[]), CAST(:ev_type AS text[]), CAST(:ev_agg AS text[]), "
        "CAST(:ev_aid AS uuid[]), CAST(:ev_ver AS integer[]), CAST(:ev_data AS text[])) "
        "AS x(seq, type, agg, aid, ver, data) RETURNING 1), "
        "ac AS ("
        "INSERT INTO activity_events (id, house_id, bill_id, actor_user_id, actor_member_id, type, payload, "
        "house_event_seq, created_at) "
        "SELECT y.id, :house, y.bill, y.usr, :actor, y.type, CAST(y.payload AS jsonb), y.seq, :now "
        "FROM unnest(CAST(:ac_id AS uuid[]), CAST(:ac_bill AS uuid[]), CAST(:ac_user AS uuid[]), "
        "CAST(:ac_type AS text[]), CAST(:ac_payload AS text[]), CAST(:ac_seq AS bigint[])) "
        "AS y(id, bill, usr, type, payload, seq) RETURNING 1), "
        "ob AS ("
        "INSERT INTO outbox (id, house_id, seq, kind, status, attempts, next_attempt_at, created_at) "
        "SELECT z.id, :house, z.seq, z.kind, 'pending', 0, :now, :now "
        "FROM unnest(CAST(:ob_id AS uuid[]), CAST(:ob_seq AS bigint[]), CAST(:ob_kind AS text[])) "
        "AS z(id, seq, kind) RETURNING 1)" + update + " "
        "SELECT (SELECT count(*) FROM ev), (SELECT count(*) FROM ac), (SELECT count(*) FROM ob)"
    )


_FINISH_SQL = {key: _finish_sql(*key) for key in ((False, False), (True, False), (True, True))}


@asynccontextmanager
async def house_write(
    ctx: WriteContext,
    house_id: uuid.UUID,
    *,
    actor_member_id: uuid.UUID | None = None,
    client_mutation_id: str | None = None,
    idempotency: Idempotency | None = None,
) -> AsyncIterator[HouseWrite]:
    async with ctx.sessions() as session, session.begin():
        write = HouseWrite(session, house_id, actor_member_id, client_mutation_id)
        write.idempotency = idempotency
        await write._claim_idempotency_key(ctx.clock.now())
        yield write
        await write._finish(ctx.clock)
        # Committed. Publish strictly after commit (§21.5).
    for event in write.committed:
        await publish_safely(ctx.bus, house_channel(house_id), event.wire())
    if write.outbox_rows:
        last = write.committed[-1]
        await kick_safely(ctx.kicker, key=f"drain-{house_id}-{last.seq}")
