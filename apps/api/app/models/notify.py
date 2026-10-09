"""Notifications, preferences and Web Push subscriptions (PRD §8, §10, §20.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Index, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import enums
from app.models.base import Base, enum_check, fk, ts, uuid_pk


class Notification(Base):
    """One row per recipient x channel; `dedupe_key = event_id:user_id:channel` (§20.2)."""

    __tablename__ = "notifications"
    __table_args__ = (
        enum_check("channel", enums.NOTIFICATION_CHANNELS),
        enum_check("type", enums.NOTIFICATION_TYPES),
        enum_check("status", enums.NOTIFICATION_STATUSES),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index(None, "user_id", "read_at"),
        Index(None, "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    house_id: Mapped[uuid.UUID | None] = fk("houses.id", nullable=True)
    event_seq: Mapped[int | None] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = ts(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts()
    sent_at: Mapped[datetime | None] = ts(nullable=True)
    read_at: Mapped[datetime | None] = ts(nullable=True)


class NotificationPref(Base):
    __tablename__ = "notification_prefs"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "type", "channel"),
        enum_check("type", enums.NOTIFICATION_TYPES),
        enum_check("channel", enums.PREF_CHANNELS),
    )

    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    type: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (CheckConstraint("failure_count >= 0", name="failure_count_nonnegative"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE", index=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    device_label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = ts()
    last_success_at: Mapped[datetime | None] = ts(nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    stale_at: Mapped[datetime | None] = ts(nullable=True)
