"""Redis pub/sub fan-out for committed house events (PRD §21.5)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Protocol

from redis.asyncio import Redis

log = logging.getLogger(__name__)


def house_channel(house_id: uuid.UUID) -> str:
    return f"house:{house_id}"


class EventBus(Protocol):
    async def publish(self, channel: str, message: dict[str, Any]) -> None: ...


class RedisBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps(message, default=str))


class NullBus:
    """Used where no Redis is configured; clients still converge via gap fill and polling."""

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        return None


async def publish_safely(bus: EventBus, channel: str, message: dict[str, Any]) -> bool:
    """A lost publish is tolerated (SYNC-18): the next event reveals the gap. Never raise."""
    try:
        await bus.publish(channel, message)
    except Exception:
        log.warning("event publish failed", extra={"channel": channel, "seq": message.get("seq")})
        return False
    return True
