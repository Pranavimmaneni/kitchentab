"""Builds the notification pipeline's dependencies from settings (API, arq worker, queue subscriber)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.email import email_sender
from app.adapters.push import WebPushSender
from app.adapters.queue import Kicker, kick_safely
from app.clock import Clock
from app.services.notify import NotifyDeps
from app.settings import Settings

P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@lru_cache(maxsize=4)
def dev_vapid_keys(secret: str) -> tuple[str, str]:
    """PUSH_PROVIDER=fake without configured keys: a key pair derived from SESSION_SECRET, so the API
    and the workers agree without a key in the repo. Never used when deployed (settings validation
    requires real VAPID keys with PUSH_PROVIDER=webpush)."""
    digest = hmac.new(secret.encode(), b"kitchentab-dev-vapid", hashlib.sha256).digest()
    scalar = int.from_bytes(digest, "big") % (P256_ORDER - 1) + 1
    key = ec.derive_private_key(scalar, ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(public), _b64(scalar.to_bytes(32, "big"))


def vapid_keys(settings: Settings) -> tuple[str, str]:
    if settings.vapid_public_key and settings.vapid_private_key:
        return settings.vapid_public_key, settings.vapid_private_key.get_secret_value()
    return dev_vapid_keys(settings.session_secret.get_secret_value())


def build_deps(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    clock: Clock,
    kicker: Kicker | None,
) -> NotifyDeps:
    _, private = vapid_keys(settings)

    async def schedule(at: datetime) -> None:
        # No idempotency key: a key per time slot dropped wake-ups whose slot was already used, and
        # a spare wake-up only costs an idempotent drain.
        await kick_safely(kicker, at=at, now=clock.now())

    return NotifyDeps(
        sessions=sessions,
        clock=clock,
        push=WebPushSender(private, settings.vapid_subject),
        email=email_sender(settings, sessions, clock),
        web_origin=settings.web_origin,
        schedule=schedule,
        unsubscribe_secret=settings.token_hash_pepper.get_secret_value(),
        postal_address=settings.email_postal_address,
    )
