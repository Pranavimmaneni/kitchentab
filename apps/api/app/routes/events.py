"""House event stream (PRD §21.5; ADR-0012): SSE from Postgres, plus the JSON catch-up.

* `GET /houses/{id}/events` with `Accept: text/event-stream`: replays `seq > Last-Event-ID`, then
  polls `house_events` every 750 ms (an indexed primary-key range scan). With PROFILE=local a
  Redis message wakes the loop early. Heartbeat comment every 20 s; the stream closes cleanly at
  270 s (Vercel Hobby's limit is 300 s) and the browser reconnects with `Last-Event-ID`.
* If the client is more than 500 events behind, or the events it needs were purged (7 days),
  the server sends `event: resync` and the client refetches everything.
* Membership is checked when the stream opens and every 5 minutes; a removed member's stream gets
  `event: revoked` and closes (SYNC-12, SYNC-33).
* `GET /houses/{id}/events?after=N` (JSON, max 500) fills gaps and backs the polling fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select

from app.deps import Actor, ctx, require_actor
from app.errors import ErrorCode, Problem
from app.models import HouseEvent
from app.policy import Action, require
from app.services.bus import house_channel
from app.services.houses import membership

router = APIRouter(tags=["events"])
POLL_SECONDS = 0.75
HEARTBEAT_SECONDS = 20.0
MAX_STREAM_SECONDS = 270.0
MEMBERSHIP_RECHECK = timedelta(minutes=5)
REPLAY_LIMIT = 500
RETENTION = timedelta(days=7)


def wire(e: HouseEvent) -> dict[str, Any]:
    return {
        "seq": e.seq,
        "type": e.type,
        "aggregate": {"type": e.aggregate_type, "id": str(e.aggregate_id), "version": e.version},
        "actor_member_id": str(e.actor_member_id) if e.actor_member_id else None,
        "client_mutation_id": e.client_mutation_id,
        "data": e.data,
    }


async def _events_after(request: Request, house_id: uuid.UUID, after: int, limit: int) -> list[HouseEvent]:
    async with ctx(request).sessions() as db:
        return list(
            (
                await db.scalars(
                    select(HouseEvent)
                    .where(HouseEvent.house_id == house_id, HouseEvent.seq > after)
                    .order_by(HouseEvent.seq)
                    .limit(limit)
                )
            ).all()
        )


async def _needs_resync(request: Request, house_id: uuid.UUID, after: int) -> bool:
    c = ctx(request)
    async with c.sessions() as db:
        row = (
            await db.execute(
                select(func.count(), func.min(HouseEvent.seq), func.min(HouseEvent.created_at)).where(
                    HouseEvent.house_id == house_id, HouseEvent.seq > after
                )
            )
        ).one()
        latest = await db.scalar(select(func.max(HouseEvent.seq)).where(HouseEvent.house_id == house_id))
    count, first_seq, first_at = row
    if count > REPLAY_LIMIT:
        return True
    if latest is not None and after < latest and first_seq is not None and first_seq > after + 1:
        return True  # the events right after `after` were purged
    return bool(first_at is not None and c.clock.now() - first_at > RETENTION)


async def _is_member(request: Request, house_id: uuid.UUID, actor: Actor) -> bool:
    async with ctx(request).sessions() as db:
        me = await membership(db, house_id, actor.user.id)
    return me is not None and me.active


def _sse(event: str | None, data: Any, event_id: int | None = None) -> bytes:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=str, separators=(',', ':'))}")
    return ("\n".join(lines) + "\n\n").encode()


@router.get("/houses/{house_id}/events", response_model=None)
async def events(
    house_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    after: int | None = None,
) -> JSONResponse | StreamingResponse:
    async with ctx(request).sessions() as db:
        me = await membership(db, house_id, actor.user.id)
    require(Action.HOUSE_READ, actor=me)

    if "text/event-stream" not in request.headers.get("accept", ""):
        if after is None:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "after is required for the JSON catch-up")
        if await _needs_resync(request, house_id, after):
            return JSONResponse({"resync": True, "events": []})
        found = await _events_after(request, house_id, after, REPLAY_LIMIT)
        return JSONResponse({"resync": False, "events": [wire(e) for e in found]})

    raw_last = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    last = int(raw_last) if raw_last and raw_last.isdigit() else None
    return StreamingResponse(
        _stream(request, house_id, actor, last),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


async def _stream(request: Request, house_id: uuid.UUID, actor: Actor, last: int | None) -> AsyncIterator[bytes]:
    c = ctx(request)
    started = time.monotonic()
    last_beat = started
    last_check = c.clock.now()
    max_seconds = float(getattr(request.app.state, "sse_max_seconds", MAX_STREAM_SECONDS))
    poll = float(getattr(request.app.state, "sse_poll_seconds", POLL_SECONDS))
    wake = asyncio.Event()
    listener = asyncio.create_task(_redis_wake(request, house_id, wake)) if c.redis is not None else None
    try:
        yield b"retry: 1000\n\n"
        if last is None:
            async with c.sessions() as db:
                last = int(
                    await db.scalar(
                        select(func.coalesce(func.max(HouseEvent.seq), 0)).where(HouseEvent.house_id == house_id)
                    )
                    or 0
                )
            yield _sse("ready", {"seq": last})
        elif await _needs_resync(request, house_id, last):
            async with c.sessions() as db:
                last = int(
                    await db.scalar(
                        select(func.coalesce(func.max(HouseEvent.seq), 0)).where(HouseEvent.house_id == house_id)
                    )
                    or 0
                )
            yield _sse("resync", {"seq": last}, last)
        else:
            yield _sse("ready", {"seq": last})  # replay follows
        while True:
            if await request.is_disconnected():
                return
            if c.clock.now() - last_check >= MEMBERSHIP_RECHECK:
                last_check = c.clock.now()
                if not await _is_member(request, house_id, actor):
                    yield _sse("revoked", {"reason": "not_a_member"})
                    return
            for event in await _events_after(request, house_id, last, REPLAY_LIMIT):
                last = event.seq
                yield _sse(None, wire(event), event.seq)
            now = time.monotonic()
            if now - started >= max_seconds:
                yield _sse("rollover", {"seq": last})  # client reconnects with Last-Event-ID
                return
            if now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                yield b": keep-alive\n\n"
            wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=poll)
    finally:
        if listener is not None:
            listener.cancel()


async def _redis_wake(request: Request, house_id: uuid.UUID, wake: asyncio.Event) -> None:
    """PROFILE=local only: a publish after commit wakes the poll loop early (ADR-0012)."""
    redis = ctx(request).redis
    assert redis is not None
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(house_channel(house_id))
        async for message in pubsub.listen():
            if message.get("type") == "message":
                wake.set()
    except asyncio.CancelledError:
        raise
    except Exception:  # Redis is only an accelerator; polling keeps working
        return
    finally:
        with contextlib.suppress(Exception):  # best-effort cleanup
            await pubsub.aclose()  # type: ignore[no-untyped-call]
