"""Wake the outbox drain after a commit (ADR-0013).

`QUEUE_PROVIDER`:
  arq     PROFILE=local: enqueue the `drain_job` on the arq worker (which also polls every 10 s)
  vercel  PROFILE=vercel: a Vercel Queues message on topic `outbox-drain`; the subscriber in
          `app/queues/outbox.py` drains. `delay=` carries quiet-hours and coalescing wake-ups.
  inline  tests: record the kick; the test drives `drain()` itself with the controllable clock

A kick is only a nudge: a lost one delays delivery until the next poll or the daily sweeper.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.settings import Settings

log = logging.getLogger("app.queue")
TOPIC = "outbox-drain"


class Kicker(Protocol):
    async def kick(self, *, delay_seconds: float = 0, key: str | None = None) -> None: ...


@dataclass
class RecordingKicker:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def kick(self, *, delay_seconds: float = 0, key: str | None = None) -> None:
        self.calls.append({"delay_seconds": delay_seconds, "key": key})


class ArqKicker:
    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._pool: Any = None

    async def kick(self, *, delay_seconds: float = 0, key: str | None = None) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(self._url))
        await self._pool.enqueue_job("drain_job", _job_id=key, _defer_by=delay_seconds or None)


class VercelKicker:
    def __init__(self, client: Any = None) -> None:
        self._client = client  # a vercel.queue.QueueClient; the SDK's default client when None

    async def kick(self, *, delay_seconds: float = 0, key: str | None = None) -> None:
        from vercel.queue import DuplicateIdempotencyKeyError, send

        sender = self._client.send if self._client is not None else send
        with contextlib.suppress(DuplicateIdempotencyKeyError):  # that wake-up is already queued
            await sender(TOPIC, {"reason": "drain"}, delay=delay_seconds or None, idempotency_key=key)


def kicker(settings: Settings) -> Kicker:
    if settings.queue == "vercel":
        return VercelKicker()
    if settings.queue == "arq" and settings.redis_url:
        return ArqKicker(settings.redis_url)
    return RecordingKicker()


async def kick_safely(
    k: Kicker | None, *, at: datetime | None = None, now: datetime | None = None, key: str | None = None
) -> None:
    """Never let a failed nudge fail the request that already committed."""
    if k is None:
        return
    delay = max((at - now).total_seconds(), 0) if at is not None and now is not None else 0
    try:
        await k.kick(delay_seconds=delay, key=key)
    except Exception:
        log.warning("queue kick failed", extra={"delay_seconds": delay})
