"""SMS one-time codes (PRD §7). The real Twilio Verify adapter lands in Milestone 3."""

from __future__ import annotations

import json
from typing import Protocol

from redis.asyncio import Redis

from app.clock import Clock

FAKE_SMS_KEY = "test:sms"


class SmsSender(Protocol):
    async def send_code(self, phone_e164: str, code: str) -> None: ...


class FakeSms:
    """Records codes in Redis so tests read them via `GET /__test/sms` (TEST_PLAN §2).

    Never logs the code (CLAUDE.md rule 8). Entries expire after an hour.
    """

    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    async def send_code(self, phone_e164: str, code: str) -> None:
        entry = json.dumps({"to": phone_e164, "code": code, "sent_at": self._clock.now().isoformat()})
        await self._redis.rpush(FAKE_SMS_KEY, entry)
        await self._redis.expire(FAKE_SMS_KEY, 3600)

    async def sent(self, to: str | None = None) -> list[dict[str, str]]:
        raw = await self._redis.lrange(FAKE_SMS_KEY, 0, -1)
        entries = [json.loads(r) for r in raw]
        return [e for e in entries if to is None or e["to"] == to]

    async def clear(self) -> None:
        await self._redis.delete(FAKE_SMS_KEY)
