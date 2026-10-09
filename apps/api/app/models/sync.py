"""House event log, outbox, idempotency store and the activity audit log (PRD §10, §21.8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import enums
from app.models.base import Base, enum_check, fk, ts, uuid_pk


class HouseEvent(Base):
    """Replay log; one row per committed house write, numbered gaplessly in commit order."""

    __tablename__ = "house_events"
    __table_args__ = (
        PrimaryKeyConstraint("house_id", "seq"),
        CheckConstraint("seq > 0", name="seq_positive"),
        Index(None, "created_at"),
    )

    house_id: Mapped[uuid.UUID] = fk("houses.id")
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int | None] = mapped_column(Integer)
    actor_member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    client_mutation_id: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = ts()


class OutboxRow(Base):
    """Reliable hand-off from a committed event to the notification workers (§20.2)."""

    __tablename__ = "outbox"
    __table_args__ = (
        enum_check("status", enums.OUTBOX_STATUSES),
        ForeignKeyConstraint(
            ["house_id", "seq"], ["house_events.house_id", "house_events.seq"], ondelete="CASCADE"
        ),
        Index(None, "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    house_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = ts()
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts()


class IdempotencyKey(Base):
    """Stored responses for retried creates and transitions; kept 24 h (§21.8)."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (PrimaryKeyConstraint("user_id", "key"), Index(None, "created_at"))

    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL while the first request is still in flight.
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = ts()


class ActivityEvent(Base):
    """Append-only audit log shown on bills and in house history (FR-43)."""

    __tablename__ = "activity_events"
    __table_args__ = (
        Index("ix_activity_events_bill_id_created_at", "bill_id", text("created_at DESC")),
        Index("ix_activity_events_house_id_created_at", "house_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    house_id: Mapped[uuid.UUID] = fk("houses.id")
    bill_id: Mapped[uuid.UUID | None] = fk("bills.id", nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = fk("users.id", nullable=True)
    actor_member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # The house_events row written in the same transaction (DATA-06: 1:1).
    house_event_seq: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = ts()
