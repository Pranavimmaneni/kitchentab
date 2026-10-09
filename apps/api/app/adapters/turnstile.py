"""Cloudflare Turnstile (PRD §7: CAPTCHA after 2 phone-code sends). Only used with phone codes.

Without a secret (local and test) the fake accepts exactly the token "pass" so tests exercise the
CAPTCHA flow without calling Cloudflare.
"""

from __future__ import annotations

import httpx

from app.settings import Settings

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
FAKE_PASS = "pass"  # noqa: S105 - test token for the local fake, refused when deployed


async def verify(settings: Settings, token: str | None, ip: str) -> bool:
    if not token:
        return False
    if settings.turnstile_secret_key is None:
        return not settings.is_deployed and token == FAKE_PASS
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            VERIFY_URL,
            data={
                "secret": settings.turnstile_secret_key.get_secret_value(),
                "response": token,
                "remoteip": ip,
            },
        )
    return bool(resp.status_code == 200 and resp.json().get("success"))
