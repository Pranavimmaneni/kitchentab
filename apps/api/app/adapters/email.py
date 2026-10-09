"""Transactional email (PRD §7, §8). EMAIL_PROVIDER=fake delivers over SMTP to Mailpit.

Real providers (Resend/Postmark/SES) land in Milestone 3. `MailpitClient` is the test-side reader.
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class EmailSender(Protocol):
    async def send(self, message: Email) -> None: ...


class SmtpEmailSender:
    """Plain SMTP; used with Mailpit locally and in tests."""

    def __init__(self, smtp_url: str, sender: str) -> None:
        parts = urlsplit(smtp_url)
        self._host = parts.hostname or "localhost"
        self._port = parts.port or 1025
        self._sender = sender

    def _send_sync(self, message: Email) -> None:
        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = message.to
        msg["Subject"] = message.subject
        for name, value in message.headers.items():
            msg[name] = value
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.send_message(msg)

    async def send(self, message: Email) -> None:
        await asyncio.to_thread(self._send_sync, message)


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
