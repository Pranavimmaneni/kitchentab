"""Platform tables that replace Redis in production (ADR-0014): rate limits and the email test inbox."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ts, uuid_pk


class RateLimitHit(Base):
    """Fixed-window counters: one row per (bucket, key, window); purged by the daily cron."""

    __tablename__ = "rate_limit_hits"
    __table_args__ = (
        PrimaryKeyConstraint("bucket", "key", "window_start"),
        CheckConstraint("count > 0", name="count_positive"),
        Index(None, "window_start"),
    )

    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)  # hashed identifier, never raw email/phone/IP
    window_start: Mapped[datetime] = ts()
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class TestInboxMessage(Base):
    """Emails captured when EMAIL_PROVIDER=testinbox (ADR-0016). Read only via /api/test/inbox."""

    __tablename__ = "test_inbox"
    __table_args__ = (Index(None, "to_address", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    to_address: Mapped[str] = mapped_column(CITEXT, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    html_body: Mapped[str | None] = mapped_column(Text)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = ts()
