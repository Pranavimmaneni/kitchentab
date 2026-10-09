"""Phone codes (PRD §7). Hidden unless SMS_PROVIDER is set (ADR-0016).

Twilio Verify generates and checks its own codes, so phone sign-in goes through a verifier:
`start(phone)` sends, `check(phone, code)` answers. The fake keeps codes in Postgres
(`test_inbox`, subject "sms") so tests read them via `GET /__test/sms` in any profile.
"""

from __future__ import annotations

import secrets
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.ids import uuid7
from app.models import TestInboxMessage
from app.settings import Settings

FAKE_SMS_SUBJECT = "sms"


class SmsVerifier(Protocol):
    async def start(self, phone_e164: str) -> None: ...
    async def check(self, phone_e164: str, code: str) -> bool: ...


class FakeSms:
    """Records codes; never logs them (CLAUDE.md rule 8)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    async def start(self, phone_e164: str) -> None:
        code = f"{secrets.randbelow(10**6):06d}"
        async with self._sessions() as db, db.begin():
            db.add(
                TestInboxMessage(
                    id=uuid7(),
                    to_address=phone_e164,
                    subject=FAKE_SMS_SUBJECT,
                    text_body=code,
                    headers={},
                    created_at=self._clock.now(),
                )
            )

    async def check(self, phone_e164: str, code: str) -> bool:
        latest = await self.sent(phone_e164)
        return bool(latest) and secrets.compare_digest(latest[0]["code"], code)

    async def sent(self, to: str | None = None) -> list[dict[str, str]]:
        """Newest first."""
        async with self._sessions() as db:
            q = select(TestInboxMessage).where(TestInboxMessage.subject == FAKE_SMS_SUBJECT)
            if to:
                q = q.where(TestInboxMessage.to_address == to)
            rows = (await db.scalars(q.order_by(TestInboxMessage.created_at.desc()))).all()
        return [{"to": r.to_address, "code": r.text_body, "sent_at": r.created_at.isoformat()} for r in rows]


class TwilioVerify:
    """Twilio Verify v2 (https://www.twilio.com/docs/verify/api). Never called in tests."""

    def __init__(self, account_sid: str, auth_token: str, service_sid: str) -> None:
        self._auth = (account_sid, auth_token)
        self._base = f"https://verify.twilio.com/v2/Services/{service_sid}"

    async def start(self, phone_e164: str) -> None:
        async with httpx.AsyncClient(timeout=10, auth=self._auth) as client:
            resp = await client.post(f"{self._base}/Verifications", data={"To": phone_e164, "Channel": "sms"})
            resp.raise_for_status()

    async def check(self, phone_e164: str, code: str) -> bool:
        async with httpx.AsyncClient(timeout=10, auth=self._auth) as client:
            resp = await client.post(f"{self._base}/VerificationCheck", data={"To": phone_e164, "Code": code})
            if resp.status_code == 404:  # expired or already approved
                return False
            resp.raise_for_status()
            return bool(resp.json().get("status") == "approved")


def sms_verifier(settings: Settings, sessions: async_sessionmaker[AsyncSession], clock: Clock) -> SmsVerifier | None:
    if settings.sms_provider == "fake":
        return FakeSms(sessions, clock)
    if settings.sms_provider == "twilio_verify":
        assert settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_verify_service_sid
        return TwilioVerify(
            settings.twilio_account_sid,
            settings.twilio_auth_token.get_secret_value(),
            settings.twilio_verify_service_sid,
        )
    return None
