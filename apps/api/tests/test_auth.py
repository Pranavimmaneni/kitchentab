"""Sign-in and sessions (PRD §7; TEST_PLAN 3.2)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuthIdentity, Session, User
from tests.harness.api import TEST_INBOX_SECRET, Api
from tests.harness.tags import fr, tid

ApiFactory = Callable[[], Api]


def _cookie_attrs(resp: httpx.Response, name: str) -> str:
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.lower()
    raise AssertionError(f"{name} not set")


@tid("AUTH-01")
@fr("FR-01")
async def test_AUTH_01_email_code_signs_in_with_safe_cookie(api: ApiFactory) -> None:
    a = api()
    sent = await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "Meera@Example.com "}, idem=False)
    assert sent.json() == {"sent": True, "masked": "m***@example.com", "resend_after_seconds": 30}
    code = await a.latest_code("meera@example.com")
    resp = await a.post(
        "/api/v1/auth/otp/verify", {"channel": "email", "destination": "meera@example.com", "code": code}, idem=False
    )
    assert resp.status_code == 200
    attrs = _cookie_attrs(resp, "kt_session")
    assert "httponly" in attrs and "secure" in attrs and "samesite=lax" in attrs
    me = (await a.get("/api/v1/me")).json()
    assert me["user"]["email"] == "meera@example.com" and me["user"]["display_name"] == "Meera"


@tid("AUTH-02")
@fr("FR-01")
async def test_AUTH_02_phone_code_signs_in(api: ApiFactory) -> None:
    a = api()
    sent = await a.post("/api/v1/auth/otp/send", {"channel": "phone", "destination": "(201) 555-0142"}, idem=False)
    assert sent.status_code == 200 and sent.json()["masked"] == "(***) ***-0142"
    [sms] = (await a.get("/__test/sms", params={"to": "+12015550142"})).json()
    resp = await a.post(
        "/api/v1/auth/otp/verify", {"channel": "phone", "destination": "2015550142", "code": sms["code"]}, idem=False
    )
    assert resp.status_code == 200
    assert (await a.get("/api/v1/me")).json()["user"]["phone"] == "+12015550142"


async def _google(a: Api, app: Any, email: str, verified: bool = True) -> httpx.Response:
    """Browser -> /auth/google/start -> mock issuer (in-process) -> /auth/google/callback."""
    start = await a.get("/api/v1/auth/google/start", params={"return_to": "/houses/new", "login_hint": email})
    assert start.status_code == 302
    query = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    query["email_verified"] = "true" if verified else "false"
    async with httpx.AsyncClient(transport=app.state.google_transport, base_url="http://oidc.test") as idp:
        redirect = await idp.get("/authorize", params=query)
    assert redirect.status_code == 302, redirect.text
    callback = urlsplit(redirect.headers["location"])
    return await a.get(f"{callback.path}?{callback.query}")


@tid("AUTH-03")
@fr("FR-01")
async def test_AUTH_03_google_with_pkce_and_unverified_rejected(api: ApiFactory, app: Any) -> None:
    a = api()
    done = await _google(a, app, "arjun@example.com")
    assert done.status_code == 302 and done.headers["location"] == "/houses/new"
    assert (await a.get("/api/v1/me")).json()["user"]["email"] == "arjun@example.com"
    b = api()
    rejected = await _google(b, app, "eve@example.com", verified=False)
    assert rejected.status_code == 403 and rejected.json()["code"] == "EMAIL_UNVERIFIED"


@tid("AUTH-04")
@fr("FR-01")
async def test_AUTH_04_same_verified_email_is_one_user(
    api: ApiFactory, app: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _google(api(), app, "kabir@example.com")
    await api().sign_in("kabir@example.com")
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        providers = set((await db.scalars(select(AuthIdentity.provider))).all())
    assert providers == {"google", "email"}


@tid("AUTH-05")
async def test_AUTH_05_wrong_code_locks_and_expired_code_rejected(api: ApiFactory) -> None:
    a = api()
    await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "a@example.com"}, idem=False)
    code = await a.latest_code("a@example.com")
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    body = {"channel": "email", "destination": "a@example.com", "code": wrong}
    messages = []
    for _ in range(5):
        r = await a.post("/api/v1/auth/otp/verify", body, idem=False)
        messages.append((r.json()["code"], r.json()["title"]))
    assert messages[0] == ("CODE_MISMATCH", "That code didn't match. 4 tries left.")
    assert messages[-1][0] == "CODE_LOCKED"
    locked = await a.post("/api/v1/auth/otp/verify", {**body, "code": code}, idem=False)
    assert locked.json()["code"] == "CODE_LOCKED", "even the right code fails once locked"

    await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "a@example.com"}, idem=False)
    fresh = await a.latest_code("a@example.com")
    await a.post("/__test/clock", {"advance_seconds": 11 * 60}, idem=False)
    expired = await a.post("/api/v1/auth/otp/verify", {**body, "code": fresh}, idem=False)
    assert expired.json()["code"] == "CODE_EXPIRED"


@tid("AUTH-06")
async def test_AUTH_06_send_limits_and_captcha(api: ApiFactory) -> None:
    a = api()
    for _ in range(5):
        assert (
            await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "x@example.com"}, idem=False)
        ).status_code == 200
    limited = await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "x@example.com"}, idem=False)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0

    phone = {"channel": "phone", "destination": "+12015550199"}
    assert (await a.post("/api/v1/auth/otp/send", phone, idem=False)).status_code == 200
    assert (await a.post("/api/v1/auth/otp/send", phone, idem=False)).status_code == 200
    captcha = await a.post("/api/v1/auth/otp/send", phone, idem=False)
    assert captcha.status_code == 403 and captcha.json()["code"] == "CAPTCHA_REQUIRED"
    assert (await a.post("/api/v1/auth/otp/send", {**phone, "turnstile_token": "pass"}, idem=False)).status_code == 200
    fourth = await a.post("/api/v1/auth/otp/send", {**phone, "turnstile_token": "pass"}, idem=False)
    assert fourth.status_code == 429, "3 per number per hour"
    # 10 per IP per day across numbers
    statuses = []
    for n in range(12):
        r = await a.post(
            "/api/v1/auth/otp/send",
            {"channel": "phone", "destination": f"+120155501{n:02d}", "turnstile_token": "pass"},
            idem=False,
        )
        statuses.append(r.status_code)
    assert 429 in statuses and statuses.count(200) <= 7  # 3 already used today from this IP


@tid("AUTH-07")
async def test_AUTH_07_codes_never_in_logs(api: ApiFactory, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    a = api()
    await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "logs@example.com"}, idem=False)
    code = await a.latest_code("logs@example.com")
    await a.post(
        "/api/v1/auth/otp/verify", {"channel": "email", "destination": "logs@example.com", "code": code}, idem=False
    )
    from app.logs import JsonFormatter

    formatter = JsonFormatter()
    rendered = "\n".join(formatter.format(r) for r in caplog.records)
    assert caplog.records, "requests were logged"
    assert code not in rendered
    assert "logs@example.com" not in rendered


@tid("AUTH-08")
async def test_AUTH_08_session_slides_and_expires_after_30_idle_days(api: ApiFactory) -> None:
    a = api()
    await a.sign_in("slide@example.com")
    await a.post("/__test/clock", {"advance_seconds": 20 * 86400}, idem=False)
    assert (await a.get("/api/v1/me")).status_code == 200  # used on day 20: slides to day 50
    await a.post("/__test/clock", {"advance_seconds": 25 * 86400}, idem=False)
    assert (await a.get("/api/v1/me")).status_code == 200  # day 45 < 50
    await a.post("/__test/clock", {"advance_seconds": 31 * 86400}, idem=False)
    assert (await a.get("/api/v1/me")).status_code == 401


async def test_sign_out_everywhere_revokes_every_session(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    phone, laptop = api(), api()
    await phone.sign_in("both@example.com")
    await laptop.sign_in("both@example.com")
    assert (await laptop.call("POST", "/api/v1/auth/logout-all", idem=False)).status_code == 204
    assert (await phone.get("/api/v1/me")).status_code == 401
    assert (await laptop.get("/api/v1/me")).status_code == 401
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Session).where(Session.revoked_at.is_(None))) == 0


@tid("AUTH-10")
async def test_AUTH_10_cross_origin_post_without_token_rejected(api: ApiFactory) -> None:
    a = api()
    await a.sign_in("csrf@example.com")
    no_token = await a.http.post("/api/v1/houses", json={"name": "x"}, headers={"Idempotency-Key": "k1"})
    assert no_token.status_code == 403
    cross = await a.call("POST", "/api/v1/houses", {"name": "x"}, headers={"Origin": "https://evil.example"})
    assert cross.status_code == 403
    same = await a.call("POST", "/api/v1/houses", {"name": "x"}, headers={"Origin": "https://app.test"})
    assert same.status_code == 201


@tid("AUTH-11")
@fr("FR-01")
async def test_AUTH_11_phone_never_merges_into_an_email_account(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await api().sign_in("one@example.com")
    b = api()
    await b.post("/api/v1/auth/otp/send", {"channel": "phone", "destination": "+12015550111"}, idem=False)
    [sms] = (await b.get("/__test/sms", params={"to": "+12015550111"})).json()
    await b.post(
        "/api/v1/auth/otp/verify", {"channel": "phone", "destination": "+12015550111", "code": sms["code"]}, idem=False
    )
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 2


@tid("SEC-12")
async def test_SEC_12_new_session_id_on_every_sign_in(api: ApiFactory) -> None:
    a = api()
    await a.sign_in("fix@example.com")
    first = a.http.cookies.get("kt_session")
    await a.sign_in("fix@example.com")
    second = a.http.cookies.get("kt_session")
    assert first and second and first != second
    stale = api()
    stale.http.cookies.set("kt_session", first, domain="app.test")
    assert (await stale.get("/api/v1/me")).status_code == 401, "the old session was revoked"


@tid("AUTH-15")
@fr("FR-01")
async def test_AUTH_15_test_inbox_guarded_by_secret(api: ApiFactory, app: Any) -> None:
    a = api()
    await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "inbox@example.com"}, idem=False)
    assert (await a.get("/api/test/inbox")).status_code == 401
    assert (await a.get("/api/test/inbox", headers={"X-Test-Secret": "t" * 63 + "x"})).status_code == 401
    ok = await a.get("/api/test/inbox", headers={"X-Test-Secret": TEST_INBOX_SECRET})
    assert ok.status_code == 200 and ok.json()[0]["to"] == "inbox@example.com"
    # Absent (404) for every other provider, so production after `make set-email` has no inbox.
    app.state.settings = app.state.settings.model_copy(update={"email_provider": "smtp"})
    assert (await a.get("/api/test/inbox", headers={"X-Test-Secret": TEST_INBOX_SECRET})).status_code == 404


@tid("AUTH-16")
async def test_AUTH_16_methods_hide_phone_and_google_without_keys(api: ApiFactory, app: Any) -> None:
    a = api()
    assert (await a.get("/api/v1/auth/methods")).json()["phone"] is True  # test env configures fakes
    app.state.settings = app.state.settings.model_copy(update={"sms_provider": "none", "google_client_id": None})
    methods = (await a.get("/api/v1/auth/methods")).json()
    assert methods == {"email": True, "phone": False, "google": False, "turnstile_site_key": None}
    off = await a.post("/api/v1/auth/otp/send", {"channel": "phone", "destination": "+12015550142"}, idem=False)
    assert off.status_code == 404 and off.json()["code"] == "METHOD_DISABLED"
    assert (await a.get("/api/v1/auth/google/start")).status_code == 404


@tid("EDGE-15-28")
async def test_EDGE_15_28_otp_brute_force_locks(api: ApiFactory) -> None:
    """Brute force on OTPs: 5 wrong tries lock the code, and sends are rate limited (§7, §13)."""
    a = api()
    await a.post("/api/v1/auth/otp/send", {"channel": "email", "destination": "brute@example.com"}, idem=False)
    for guess in range(6):
        r = await a.post(
            "/api/v1/auth/otp/verify",
            {"channel": "email", "destination": "brute@example.com", "code": f"{guess:06d}"},
            idem=False,
        )
    assert r.json()["code"] == "CODE_LOCKED"
