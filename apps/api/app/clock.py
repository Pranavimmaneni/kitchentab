"""Injected server time (TEST_PLAN §2 "Clock control").

All server code reads time from a `Clock`, never from `datetime.now()` or database `now()`.
In tests `POST /__test/clock` pins or shifts it; the override is mirrored to Redis so the worker
process can follow the same time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

REDIS_CLOCK_KEY = "test:clock:override"


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ControllableClock:
    """Follows system time until pinned (`set`) or shifted (`advance`)."""

    def __init__(self, base: Clock | None = None) -> None:
        self._base = base or SystemClock()
        self._pinned: datetime | None = None
        self._offset = timedelta(0)

    def now(self) -> datetime:
        if self._pinned is not None:
            return self._pinned + self._offset
        return self._base.now() + self._offset

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("clock times must be timezone-aware")
        self._pinned = when.astimezone(UTC)
        self._offset = timedelta(0)

    def advance(self, delta: timedelta) -> None:
        self._offset += delta

    def reset(self) -> None:
        self._pinned = None
        self._offset = timedelta(0)

    def dump(self) -> str:
        """Serialized override for other processes ('' means plain system time)."""
        if self._pinned is None and not self._offset:
            return ""
        pinned = self._pinned.isoformat() if self._pinned else ""
        return f"{pinned}|{self._offset.total_seconds()}"

    def load(self, value: str | None) -> None:
        self.reset()
        if not value:
            return
        pinned, _, offset = value.partition("|")
        if pinned:
            self._pinned = datetime.fromisoformat(pinned)
        self._offset = timedelta(seconds=float(offset or 0))
