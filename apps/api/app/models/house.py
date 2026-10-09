"""Houses and members (PRD §5.1, §7, §10, §19.7, §21.8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import enums
from app.models.base import Base, enum_check, fk, ts, uuid_pk, version_col


class House(Base):
    __tablename__ = "houses"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 40", name="name_length"),
        CheckConstraint("event_seq >= 0", name="event_seq_nonnegative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    invite_token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    invite_rotated_at: Mapped[datetime | None] = ts(nullable=True)
    created_by: Mapped[uuid.UUID] = fk("users.id")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'America/New_York'"))
    # Gapless per-house event counter (§21.2). Only app.services.house_write may touch it.
    event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    version: Mapped[int] = version_col()
    created_at: Mapped[datetime] = ts()
    archived_at: Mapped[datetime | None] = ts(nullable=True)


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        enum_check("role", enums.MEMBER_ROLES),
        enum_check("status", enums.MEMBER_STATUSES),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 24", name="display_name_length"),
        CheckConstraint(
            "(status <> 'placeholder' OR user_id IS NULL) AND (status <> 'active' OR user_id IS NOT NULL)",
            name="status_matches_user",
        ),
        # A user is at most one member per house; rejoining reactivates the same row.
        UniqueConstraint("house_id", "user_id"),
        # Names unique per house (case-insensitive) among non-removed, non-anonymized members.
        Index(
            "uq_members_house_id_lower_display_name",
            "house_id",
            func.lower(text("display_name")),
            unique=True,
            postgresql_where=text("status <> 'removed' AND anonymized_at IS NULL"),
        ),
        # At most one active admin per house; "exactly one" is enforced by the service layer.
        Index(
            "uq_members_one_admin_per_house",
            "house_id",
            unique=True,
            postgresql_where=text("role = 'admin' AND status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    house_id: Mapped[uuid.UUID] = fk("houses.id")
    user_id: Mapped[uuid.UUID | None] = fk("users.id", nullable=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'member'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'placeholder'"))
    version: Mapped[int] = version_col()
    created_at: Mapped[datetime] = ts()
    joined_at: Mapped[datetime | None] = ts(nullable=True)
    anonymized_at: Mapped[datetime | None] = ts(nullable=True)
