"""Transactional email (PRD §7, §8; ADR-0016).

EMAIL_PROVIDER:
* `fake`      plain SMTP to Mailpit (local and tests); `MailpitClient` reads it back.
* `smtp`      Gmail: STARTTLS on 587 with an app password.
* `testinbox` stored in Postgres, readable only at /api/test/inbox with the secret.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.ids import uuid7
from app.models import TestInboxMessage
from app.settings import Settings


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class EmailSender(Protocol):
    async def send(self, message: Email) -> None: ...


def _build(sender: str, message: Email) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = message.to
    msg["Subject"] = message.subject
    for name, value in message.headers.items():
        msg[name] = value
    msg.set_content(message.text)
    if message.html:
        msg.add_alternative(message.html, subtype="html")
    return msg


class SmtpEmailSender:
    """Plain SMTP without auth: Mailpit locally and in tests (EMAIL_PROVIDER=fake)."""

    def __init__(self, smtp_url: str, sender: str) -> None:
        parts = urlsplit(smtp_url)
        self._host = parts.hostname or "localhost"
        self._port = parts.port or 1025
        self._sender = sender

    def _send_sync(self, message: Email) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.send_message(_build(self._sender, message))

    async def send(self, message: Email) -> None:
        await asyncio.to_thread(self._send_sync, message)


class AuthenticatedSmtpSender:
    """Gmail (EMAIL_PROVIDER=smtp): STARTTLS, then login with the app password."""

    def __init__(self, host: str, port: int, user: str, password: str, sender: str, *, starttls: bool = True) -> None:
        self._host, self._port, self._user, self._password = host, port, user, password
        self._sender = sender
        self._starttls = starttls

    def _send_sync(self, message: Email) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
            if self._starttls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self._user, self._password)
            smtp.send_message(_build(self._sender, message))

    async def send(self, message: Email) -> None:
        await asyncio.to_thread(self._send_sync, message)


class TestInboxSender:
    """EMAIL_PROVIDER=testinbox: store instead of sending (ADR-0016)."""

    __test__ = False  # not a pytest class

    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    async def send(self, message: Email) -> None:
        async with self._sessions() as db, db.begin():
            db.add(
                TestInboxMessage(
                    id=uuid7(),
                    to_address=message.to,
                    subject=message.subject,
                    text_body=message.text,
                    html_body=message.html,
                    headers=dict(message.headers),
                    created_at=self._clock.now(),
                )
            )


def email_sender(settings: Settings, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> EmailSender:
    if settings.email_provider == "testinbox":
        return TestInboxSender(sessions, clock)
    if settings.email_provider == "smtp":
        assert settings.smtp_host and settings.smtp_user and settings.smtp_password  # validated at boot
        return AuthenticatedSmtpSender(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_user,
            settings.smtp_password.get_secret_value(),
            settings.email_from,
        )
    return SmtpEmailSender(settings.smtp_url, settings.email_from)


class MailpitClient:
    """Reads what Mailpit captured (https://mailpit.axllent.org/docs/api-v1/)."""

    def __init__(self, base_url: str = "http://localhost:8025") -> None:
        self._base = base_url.rstrip("/")

    async def messages(self, to: str | None = None) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10) as client:
            params = {"query": f"to:{to}"} if to else None
            path = "/api/v1/search" if to else "/api/v1/messages"
            resp = await client.get(f"{self._base}{path}", params=params)
            resp.raise_for_status()
            return list(resp.json().get("messages", []))

    async def message(self, message_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base}/api/v1/message/{message_id}")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    async def clear(self) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            (await client.delete(f"{self._base}/api/v1/messages")).raise_for_status()
