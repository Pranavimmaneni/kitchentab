"""Sign-in (PRD §7; ADR-0016): one-time codes, account resolution, sessions.

Rules:
* A verified email from any method resolves to the same user (FR-01, AUTH-04).
* A phone is its own login method; never auto-merge two existing users (AUTH-11).
* Codes: 6 digits, 10 minutes, single use, 5 wrong tries lock the code (AUTH-05).
* Every sign-in creates a new session id (SEC-12).
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.models import AuthIdentity, OtpCode, Session, User
from app.security import new_session_token, peppered

Channel = Literal["email", "phone"]
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
SESSION_TTL = timedelta(days=30)
EXTERNAL_CODE = "external"  # phone codes are generated and checked by the SMS verifier


@dataclass(frozen=True)
class CodeCheck:
    row_id: uuid.UUID | None
    ok: bool
    tries_left: int


def destination_hash(channel: Channel, destination: str, pepper: str) -> str:
    return peppered(f"{channel}:{destination}", pepper)


async def store_code(
    db: AsyncSession, channel: Channel, destination: str, code: str | None, pepper: str, now: datetime
) -> None:
    """Invalidate earlier unused codes for this destination, then store the new one (hashed)."""
    dest = destination_hash(channel, destination, pepper)
    await db.execute(
        update(OtpCode).where(OtpCode.destination_hash == dest, OtpCode.consumed_at.is_(None)).values(consumed_at=now)
    )
    db.add(
        OtpCode(
            id=uuid7(),
            channel=channel,
            destination_hash=dest,
            code_hash=peppered(code, pepper) if code is not None else EXTERNAL_CODE,
            created_at=now,
            expires_at=now + CODE_TTL,
        )
    )


async def latest_code(db: AsyncSession, channel: Channel, destination: str, pepper: str) -> OtpCode | None:
    return (
        await db.scalars(
            select(OtpCode)
            .where(
                OtpCode.destination_hash == destination_hash(channel, destination, pepper),
                OtpCode.consumed_at.is_(None),
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
            .with_for_update(key_share=True)
        )
    ).first()


def code_problem(row: OtpCode | None, now: datetime) -> Problem | None:
    if row is None or row.expires_at <= now:
        return Problem(400, ErrorCode.CODE_EXPIRED, "Code expired.", "Send a new one.")
    if row.attempts >= MAX_ATTEMPTS:
        return Problem(400, ErrorCode.CODE_LOCKED, "Too many tries. Get a new code.")
    return None


def code_matches(row: OtpCode, code: str, pepper: str) -> bool:
    return hmac.compare_digest(row.code_hash, peppered(code, pepper))


async def resolve_user(
    db: AsyncSession,
    provider: Literal["email", "phone", "google"],
    subject: str,
    *,
    email: str | None,
    phone: str | None,
    display_name: str,
    now: datetime,
) -> User:
    """Find the user for a verified identity, link by verified email, or create one."""
    identity = (
        await db.scalars(
            select(AuthIdentity).where(AuthIdentity.provider == provider, AuthIdentity.provider_subject == subject)
        )
    ).first()
    if identity is not None:
        user = await db.get(User, identity.user_id)
        if user is None or user.deleted_at is not None:
            raise Problem(403, ErrorCode.FORBIDDEN, "This account was deleted")
        return user

    user = None
    if email is not None:
        user = (await db.scalars(select(User).where(User.email == email, User.deleted_at.is_(None)))).first()
    elif phone is not None:
        user = (await db.scalars(select(User).where(User.phone_e164 == phone, User.deleted_at.is_(None)))).first()
    if user is None:
        user = User(
            id=uuid7(),
            display_name=display_name[:60] or "Roommate",
            email=email,
            email_verified_at=now if email else None,
            phone_e164=phone,
            phone_verified_at=now if phone else None,
            created_at=now,
        )
        db.add(user)
        await db.flush()
    elif email is not None and user.email_verified_at is None:
        user.email_verified_at = now
    db.add(AuthIdentity(id=uuid7(), user_id=user.id, provider=provider, provider_subject=subject, created_at=now))
    await db.flush()
    return user


async def start_session(
    db: AsyncSession, user: User, now: datetime, *, user_agent: str | None, ip_hash: str | None
) -> str:
    token, token_hash = new_session_token()
    db.add(
        Session(
            id=uuid7(),
            user_id=user.id,
            token_hash=token_hash,
            user_agent=(user_agent or "")[:300] or None,
            ip_hash=ip_hash,
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_TTL,
        )
    )
    return token


def default_name(email: str | None) -> str:
    if not email:
        return "Roommate"
    local = email.split("@", 1)[0]
    word = "".join(ch for ch in local.split(".")[0].split("+")[0] if ch.isalpha())
    return word[:24].title() or "Roommate"
