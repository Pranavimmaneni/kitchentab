"""A signed-in API client for tests: CSRF double-submit, Idempotency-Key on POSTs, email-code
sign-in through the test inbox (the same path the production checks use, ADR-0016)."""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx

TEST_INBOX_SECRET = "t" * 64
BASE_URL = "https://app.test"


class Api:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.http = client
        self.csrf: str | None = None
        self.user_id: str | None = None
        self.email: str | None = None

    async def ensure_csrf(self) -> str:
        if self.csrf is None or self.http.cookies.get("kt_csrf") != self.csrf:
            self.csrf = (await self.http.get("/api/v1/auth/csrf")).json()["token"]
        return self.csrf

    async def call(
        self,
        method: str,
        url: str,
        json: Any = None,
        *,
        key: str | None = None,
        idem: bool | None = None,
        if_match: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        h = dict(headers or {})
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            h["X-CSRF-Token"] = await self.ensure_csrf()
        if (idem if idem is not None else method == "POST") and "Idempotency-Key" not in h:
            h["Idempotency-Key"] = key or str(uuid.uuid4())
        if if_match is not None:
            h["If-Match"] = str(if_match)
        return await self.http.request(method, url, json=json, headers=h)

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.http.get(url, **kw)

    async def post(self, url: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.call("POST", url, json, **kw)

    async def latest_code(self, email: str) -> str:
        resp = await self.http.get(
            "/api/test/inbox", params={"to": email, "limit": 1}, headers={"X-Test-Secret": TEST_INBOX_SECRET}
        )
        resp.raise_for_status()
        [message] = resp.json()
        match = re.search(r"\b(\d{6})\b", message["subject"])
        assert match, message["subject"]
        return match.group(1)

    async def sign_in(self, email: str) -> dict[str, Any]:
        sent = await self.post("/api/v1/auth/otp/send", {"channel": "email", "destination": email}, idem=False)
        assert sent.status_code == 200, sent.text
        code = await self.latest_code(email)
        resp = await self.post(
            "/api/v1/auth/otp/verify", {"channel": "email", "destination": email, "code": code}, idem=False
        )
        assert resp.status_code == 200, resp.text
        self.csrf = None  # sign-in rotates the CSRF cookie
        self.user_id = resp.json()["user_id"]
        self.email = email
        data: dict[str, Any] = resp.json()
        return data

    async def create_house(self, name: str = "Apt 4B", roommates: list[str] | None = None) -> dict[str, Any]:
        resp = await self.post(
            "/api/v1/houses",
            {"name": name, "roommates": roommates if roommates is not None else ["Arjun", "Meera", "Kabir"]},
        )
        assert resp.status_code == 201, resp.text
        data: dict[str, Any] = resp.json()
        return data
