"""Security checks at API level (TEST_PLAN 3.10, SEC-*)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from app.ids import uuid7
from app.main import create_app
from app.settings import Settings
from tests.harness.api import BASE_URL, Api
from tests.harness.tags import tid

ApiFactory = Callable[[], Api]


@pytest.fixture
async def limited(settings: Settings, db_reset: None) -> AsyncIterator[Callable[[], Api]]:
    """The app with the production limits (the shared test app turns them off for SYNC x200)."""
    app = create_app(settings.model_copy(update={"rate_limit_writes_per_min": 60, "rate_limit_reads_per_min": 300}))
    clients: list[httpx.AsyncClient] = []

    def make() -> Api:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)
        clients.append(c)
        return Api(c)

    async with app.router.lifespan_context(app):
        yield make
    for c in clients:
        await c.aclose()


async def _at_minute_start(who: Api) -> None:
    await who.post("/__test/clock", {"now": "2026-10-09T14:00:00-04:00"}, idem=False)


@tid("SEC-05", "VERCEL-05")
async def test_SEC_05_sixty_writes_a_minute_per_user(limited: Callable[[], Api]) -> None:
    phone, laptop = limited(), limited()
    email = f"busy+{uuid7()}@example.com"
    await phone.sign_in(email)
    await laptop.sign_in(email)  # the limit is per user, across devices
    await _at_minute_start(phone)
    codes: list[int] = []
    for n in range(61):
        who = phone if n % 2 else laptop
        r = await who.call("PATCH", "/api/v1/me", {"display_name": f"Busy {n}"})
        codes.append(r.status_code)
    assert codes[:60] == [200] * 60 and codes[60] == 429
    blocked = await phone.call("PATCH", "/api/v1/me", {"display_name": "Again"})
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    assert (await phone.get("/api/v1/me")).status_code == 200, "reads are counted separately"
    await phone.post("/__test/clock", {"advance_seconds": 60}, idem=False)
    assert (await phone.call("PATCH", "/api/v1/me", {"display_name": "Later"})).status_code == 200


@tid("SEC-05", "VERCEL-05")
async def test_SEC_05_three_hundred_reads_a_minute_per_ip(limited: Callable[[], Api]) -> None:
    me = limited()
    await me.sign_in(f"reader+{uuid7()}@example.com")
    await _at_minute_start(me)
    statuses: list[Any] = []
    for _ in range(301):
        statuses.append((await me.get("/api/v1/me")).status_code)
    assert statuses[:300] == [200] * 300 and statuses[300] == 429
    r = await me.get("/api/v1/me")
    assert r.status_code == 429 and r.headers["Retry-After"]


@tid("SEC-03")
async def test_SEC_03_api_responses_carry_security_headers(api: ApiFactory, settings: Settings) -> None:
    me = api()
    await me.sign_in(f"headers+{uuid7()}@example.com")
    for r in (await me.get("/api/v1/me"), await me.get("/api/v1/health"), await me.get("/api/v1/nope")):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert "unsafe-inline" not in r.headers["Content-Security-Policy"]
        assert r.headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in r.headers, "HSTS only on deployed HTTPS origins"
    from starlette.responses import Response

    from app.main import _security_headers

    resp = Response()
    _security_headers(resp, settings.model_copy(update={"app_env": "prod"}))
    assert resp.headers["Strict-Transport-Security"].startswith("max-age=63072000")
