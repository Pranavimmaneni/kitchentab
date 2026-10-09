"""Users, sign-in identities, sessions, one-time codes, payment handles (PRD §7, §10, §19.7, §20.2)."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, Text, Time, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models import enums
from app.models.base import Base, enum_check, fk, ts, uuid_pk


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        enum_check("theme", enums.THEMES),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 60", name="display_name_length"),
        CheckConstraint(
            "phone_e164 IS NULL OR phone_e164 ~ '^\\+[1-9][0-9]{6,14}$'", name="phone_e164_format"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True)
    email_verified_at: Mapped[datetime | None] = ts(nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(Text, unique=True)
    phone_verified_at: Mapped[datetime | None] = ts(nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'system'"))
    notify_email: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'America/New_York'"))
    # Fridge and pantry settings (§19.7)
    share_by_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    notify_kitchen_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Push settings (§20.2). Quiet hours are on by default; NULL start/end means off.
    quiet_start: Mapped[time | None] = mapped_column(Time, server_default=text("'22:00'"))
    quiet_end: Mapped[time | None] = mapped_column(Time, server_default=text("'08:00'"))
    private_lock_screen: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    prompt_snoozed_until: Mapped[datetime | None] = ts(nullable=True)
    created_at: Mapped[datetime] = ts()
    deleted_at: Mapped[datetime | None] = ts(nullable=True)


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject"),
        enum_check("provider", enums.AUTH_PROVIDERS),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE", index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = ts()


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE", index=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts()
    last_seen_at: Mapped[datetime] = ts()
    expires_at: Mapped[datetime] = ts()
    revoked_at: Mapped[datetime | None] = ts(nullable=True)


class OtpCode(Base):
    __tablename__ = "otp_codes"
    __table_args__ = (
        enum_check("channel", enums.OTP_CHANNELS),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index(None, "channel", "destination_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    destination_hash: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = ts()
    expires_at: Mapped[datetime] = ts()
    consumed_at: Mapped[datetime | None] = ts(nullable=True)


class PaymentHandle(Base):
    __tablename__ = "payment_handles"
    __table_args__ = (
        UniqueConstraint("user_id", "method"),
        enum_check("method", enums.PAYMENT_HANDLE_METHODS),
        CheckConstraint("char_length(handle) BETWEEN 1 AND 120", name="handle_length"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    method: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()
