"""Event recorder (TEST_PLAN §2): records every event published for a house and checks ordering.

Milestone 1 listens on the Redis channel `house:{id}` (what the API publishes after commit).
Milestone 6 adds an SSE mode that reads `GET /houses/{id}/events` with the same assertions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from types import TracebackType
from typing import Any

from redis.asyncio import Redis

from app.services.bus import house_channel


class EventRecorder:
    def __init__(self, redis: Redis, house_id: uuid.UUID) -> None:
        self._redis = redis
        self._channel = house_channel(house_id)
        self.events: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._changed = asyncio.Condition()

    async def __aenter__(self) -> EventRecorder:
        self._task = asyncio.create_task(self._listen())
        await asyncio.wait_for(self._ready.wait(), 5)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        self._ready.set()
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                async with self._changed:
                    self.events.append(json.loads(message["data"]))
                    self._changed.notify_all()
        finally:
            await pubsub.unsubscribe(self._channel)
            await pubsub.aclose()

    async def wait_for(self, count: int, timeout: float = 5.0) -> list[dict[str, Any]]:
        async def _enough() -> None:
            async with self._changed:
                await self._changed.wait_for(lambda: len(self.events) >= count)

        await asyncio.wait_for(_enough(), timeout)
        return self.events

    async def settle(self, quiet: float = 0.2) -> list[dict[str, Any]]:
        """Wait until no new event arrives for `quiet` seconds."""
        seen = -1
        while seen != len(self.events):
            seen = len(self.events)
            await asyncio.sleep(quiet)
        return self.events

    @property
    def seqs(self) -> list[int]:
        return [int(e["seq"]) for e in self.events]

    def assert_gapless(self, start: int = 1) -> None:
        """Every number start..N seen exactly once: no gaps, no duplicates (SYNC-25).

        Pub/sub delivery order can differ from commit order when writers publish concurrently,
        which is why clients reorder by `seq` and fill gaps (§21.5). Commit order itself is
        checked against Postgres commit timestamps in the SYNC-25 test.
        """
        expected = list(range(start, start + len(self.events)))
        assert sorted(self.seqs) == expected, f"gaps or duplicates: {sorted(self.seqs)}"

    def assert_in_order(self) -> None:
        """Delivered in sequence order (for ordered transports such as the SSE replay, Milestone 6)."""
        assert self.seqs == sorted(self.seqs), f"out of order: {self.seqs}"
