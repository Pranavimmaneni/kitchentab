"""Retention purges run by the single daily Vercel cron (PRD §13 Retention, §21.8; ADR-0013).

OTP rows after 24 h, idempotency keys after 24 h, house events after 7 days (their done outbox
rows go with them), rate-limit windows after 2 days, test-inbox messages after 7 days, sessions
30 days after they expired or were revoked. Receipt images (2 years after settle) are purged by
the storage adapter in Milestone 5. Idempotent: running twice deletes nothing more.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RULES: list[tuple[str, str, timedelta]] = [
    ("otp_codes", "DELETE FROM otp_codes WHERE created_at < :cutoff", timedelta(hours=24)),
    ("idempotency_keys", "DELETE FROM idempotency_keys WHERE created_at < :cutoff", timedelta(hours=24)),
    (
        "house_events",
        "DELETE FROM house_events e WHERE e.created_at < :cutoff AND NOT EXISTS "
        "(SELECT 1 FROM outbox o WHERE o.house_id = e.house_id AND o.seq = e.seq "
        "AND o.status IN ('pending', 'processing'))",
        timedelta(days=7),
    ),
    ("rate_limit_hits", "DELETE FROM rate_limit_hits WHERE window_start < :cutoff", timedelta(days=2)),
    ("test_inbox", "DELETE FROM test_inbox WHERE created_at < :cutoff", timedelta(days=7)),
    (
        "sessions",
        "DELETE FROM sessions WHERE coalesce(revoked_at, expires_at) < :cutoff",
        timedelta(days=30),
    ),
]


async def purge_expired(db: AsyncSession, now: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, sql, age in RULES:
        result = await db.execute(text(sql), {"cutoff": now - age})
        counts[name] = int(getattr(result, "rowcount", 0) or 0)
    return counts
