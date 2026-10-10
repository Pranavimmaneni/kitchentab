"""Retention purges run by the single daily Vercel cron (PRD §13 Retention, §21.8; ADR-0013).

OTP rows after 24 h, idempotency keys after 24 h, house events after 7 days (their done outbox
rows go with them), rate-limit windows after 2 days, test-inbox messages after 7 days, sessions
30 days after they expired or were revoked. Receipt files are deleted from storage 2 years after
their bill settled (DATA-03); the `receipts` row stays (soft-deleted) for the bill's history.
Idempotent: running twice deletes nothing more.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import Storage, StorageError

log = logging.getLogger(__name__)
RECEIPT_BATCH = 500  # per daily run; the cron's 300 s budget (ADR-0013)

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


async def purge_expired(db: AsyncSession, now: datetime, storage: Storage | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, sql, age in RULES:
        result = await db.execute(text(sql), {"cutoff": now - age})
        counts[name] = int(getattr(result, "rowcount", 0) or 0)
    if storage is not None:
        counts["receipt_files"] = await purge_receipt_files(db, now, storage)
    return counts


async def purge_receipt_files(db: AsyncSession, now: datetime, storage: Storage) -> int:
    """Receipt images 2 years (calendar) after the bill settled (PRD §13 Retention). A file that
    can't be deleted now is retried on the next run; a missing file counts as deleted."""
    rows = (
        await db.execute(
            text(
                "SELECT r.id, r.storage_key FROM receipts r JOIN bills b ON b.id = r.bill_id "
                "WHERE r.deleted_at IS NULL AND b.state = 'settled' "
                "AND b.settled_at < CAST(:now AS timestamptz) - interval '2 years' "
                "ORDER BY b.settled_at, r.id LIMIT :limit FOR UPDATE OF r SKIP LOCKED"
            ),
            {"now": now, "limit": RECEIPT_BATCH},
        )
    ).all()
    deleted = 0
    for receipt_id, key in rows:
        try:
            await storage.delete(key)
        except StorageError as exc:
            log.warning("receipt file delete failed, retrying next run: %s", exc)
            continue
        await db.execute(text("UPDATE receipts SET deleted_at = :now WHERE id = :id"), {"now": now, "id": receipt_id})
        deleted += 1
    return deleted
