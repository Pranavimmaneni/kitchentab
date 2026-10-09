"""Fixed-window rate limits in Postgres (ADR-0014). Keys are hashed; never raw emails, phones or IPs.

    await hit(session, clock, "otp_email", key_hash, limit=5, window=timedelta(hours=1))

`hit` counts the attempt and raises 429 RATE_LIMITED with Retry-After when the window is full.
`count` only reads (for "CAPTCHA after 2 sends").
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import Clock
from app.errors import ErrorCode, Problem


def window_start(now: datetime, window: timedelta) -> datetime:
    seconds = int(window.total_seconds())
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=now.tzinfo)


async def hit(session: AsyncSession, clock: Clock, bucket: str, key: str, *, limit: int, window: timedelta) -> int:
    now = clock.now()
    start = window_start(now, window)
    row = (
        await session.execute(
            text(
                "INSERT INTO rate_limit_hits (bucket, key, window_start, count) VALUES (:b, :k, :w, 1) "
                "ON CONFLICT (bucket, key, window_start) DO UPDATE SET count = rate_limit_hits.count + 1 "
                "RETURNING count"
            ),
            {"b": bucket, "k": key, "w": start},
        )
    ).scalar_one()
    if row > limit:
        retry = max(1, int((start + window - now).total_seconds()))
        raise Problem(
            429,
            ErrorCode.RATE_LIMITED,
            "Too many tries",
            f"Try again in {max(1, retry // 60)} min.",
            extra={"retry_after": retry},
            headers={"Retry-After": str(retry)},
        )
    return int(row)


async def count(session: AsyncSession, clock: Clock, bucket: str, key: str, *, window: timedelta) -> int:
    start = window_start(clock.now(), window)
    value = (
        await session.execute(
            text("SELECT count FROM rate_limit_hits WHERE bucket = :b AND key = :k AND window_start = :w"),
            {"b": bucket, "k": key, "w": start},
        )
    ).scalar_one_or_none()
    return int(value or 0)
