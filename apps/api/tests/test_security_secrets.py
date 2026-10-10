"""SEC-07: secrets never reach the logs (TEST_PLAN 3.10; CLAUDE.md rule 8).

A representative flow runs against an app logging at DEBUG: email, phone and Google sign-in, a house
with an invite claim, a sent tab opened from its link, payments, push subscriptions with real keys
and a real Web Push send to the fake push service, the cron entry points with right and wrong
secrets, the test inbox, a wrong code and sign-out. Then:

* the JSON log output (what ships to stdout) contains none of: session tokens, CSRF tokens, OTP
  codes, push endpoints and keys, invite and bill-link tokens, full email addresses, phone numbers,
  TEST_INBOX_SECRET, CRON_SECRET, SESSION_SECRET, TOKEN_HASH_PEPPER, the Google client secret or
  the VAPID private key;
* the app's own log calls (`app.*` loggers) never pass those values in the first place, so the
  redaction in `app.logs` stays a safety net, not the control.

The client-bundle half of SEC-07 is `scripts/scan_bundle_secrets.py` (run by `make test-security`).
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import httpx
import pytest

from app.logs import JsonFormatter, redact_text, request_id_var
from app.main import create_app
from app.services.notify_runtime import vapid_keys
from app.settings import Settings
from fakeservices.fake_push import create_app as create_fake_push
from tests.harness.api import BASE_URL, TEST_INBOX_SECRET, Api
from tests.harness.servers import free_port, serve
from tests.harness.tags import tid

PHONE = "+12125550142"


@pytest.fixture(scope="module")
def push_service() -> Iterator[str]:
    port = free_port()
    with serve(create_fake_push(f"http://127.0.0.1:{port}"), port) as url:
        yield url


@pytest.fixture
async def debug_app(settings: Settings, db_reset: None) -> AsyncIterator[tuple[Any, Callable[[], Api]]]:
    """The app as deployed but logging everything (LOG_LEVEL=DEBUG); restores logging afterwards."""
    from fakeservices.mock_oidc import create_app as create_mock_oidc

    root = logging.getLogger()
    saved = (root.level, list(root.handlers))
    app = create_app(settings.model_copy(update={"log_level": "DEBUG"}))
    app.state.google_transport = httpx.ASGITransport(app=create_mock_oidc(settings.google_issuer))
    clients: list[httpx.AsyncClient] = []

    def make() -> Api:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, follow_redirects=False)
        clients.append(c)
        return Api(c)

    try:
        async with app.router.lifespan_context(app):
            yield app, make
    finally:
        for c in clients:
            await c.aclose()
        root.setLevel(saved[0])
        root.handlers[:] = saved[1]


async def _google(who: Api, app: Any, email: str) -> None:
    start = await who.get("/api/v1/auth/google/start", params={"return_to": "/tabs", "login_hint": email})
    assert start.status_code == 302, start.text
    query = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    query["email_verified"] = "true"
    async with httpx.AsyncClient(transport=app.state.google_transport, base_url="http://oidc.test") as idp:
        redirect = await idp.get("/authorize", params=query)
    assert redirect.status_code == 302, redirect.text
    callback = urlsplit(redirect.headers["location"])
    done = await who.get(f"{callback.path}?{callback.query}")
    assert done.status_code == 302, done.text


async def _codes(who: Api) -> list[str]:
    inbox = await who.http.get("/api/test/inbox", params={"limit": 100}, headers={"X-Test-Secret": TEST_INBOX_SECRET})
    found = {m for msg in inbox.json() for m in re.findall(r"\b\d{6}\b", msg["subject"])}
    sms = (await who.get("/__test/sms")).json()
    return sorted(found | {s["code"] for s in sms})


async def _register_push(who: Api, base: str, label: str) -> dict[str, Any]:
    data = httpx.post(f"{base}/subscriptions", json={"user": label}).json()
    resp = await who.post(
        "/api/v1/push/subscriptions", {"endpoint": data["endpoint"], "keys": data["keys"]}, idem=False
    )
    assert resp.status_code == 201, resp.text
    return dict(data)


async def run_flow(app: Any, make: Callable[[], Api], push: str, settings: Settings) -> dict[str, str]:
    """Returns every secret the flow touched, by name."""
    tag = uuid.uuid4().hex[:8]
    secrets: dict[str, str] = {}
    admin, member, googler, anon = make(), make(), make(), make()
    emails = {"admin": f"pranav.{tag}@example.com", "member": f"meera.{tag}@example.com"}
    await admin.sign_in(emails["admin"])
    await member.sign_in(emails["member"])
    await admin.post("/__test/clock", {"now": "2026-10-09T14:00:00-04:00"}, idem=False)  # daytime: push sends
    # wrong code first, then a phone attached with the fake SMS code
    wrong = await anon.post(
        "/api/v1/auth/otp/verify", {"channel": "email", "destination": emails["admin"], "code": "000001"}, idem=False
    )
    assert wrong.status_code == 400
    started = await member.post("/api/v1/me/identities/start", {"channel": "phone", "destination": "(212) 555-0142"})
    assert started.status_code == 200, started.text
    sms = (await member.get("/__test/sms", params={"to": PHONE})).json()
    verified = await member.post(
        "/api/v1/me/identities/verify", {"channel": "phone", "destination": PHONE, "code": sms[0]["code"]}
    )
    assert verified.status_code == 201, verified.text
    emails["google"] = f"kabir.{tag}@example.com"
    await _google(googler, app, emails["google"])

    house = await admin.create_house(f"Logs {tag}", ["Meera", "Kabir"])
    members = {m["display_name"]: m["id"] for m in house["members"]}
    invite = house["invite_token"]
    assert (await anon.get(f"/api/v1/join/{invite}")).status_code == 200
    claim = await member.post(f"/api/v1/join/{invite}/claim", {"member_id": members["Meera"]})
    assert claim.status_code == 200, claim.text
    phones = {
        "admin": await _register_push(admin, push, f"admin-{tag}"),
        "member": await _register_push(member, push, f"member-{tag}"),
    }
    hid = house["house"]["id"]
    bill = (await admin.post(f"/api/v1/houses/{hid}/bills", {"store_name": "ShopRite"})).json()
    item = (await admin.post(f"/api/v1/bills/{bill['id']}/items", {"name": "Milk", "line_total_cents": 400})).json()
    changes = [
        {"item_id": item["id"], "participant": f"m:{m}", "on": True} for m in (house["member_id"], members["Meera"])
    ]
    assert (await admin.call("PUT", f"/api/v1/bills/{bill['id']}/allocations", {"changes": changes})).status_code == 200
    sent = await admin.post(f"/api/v1/bills/{bill['id']}/send")
    assert sent.status_code == 200, sent.text
    link = sent.json()["link_token"]
    assert (await anon.get(f"/api/v1/b/{link}")).status_code == 200
    assert (await member.get(f"/api/v1/b/{link}")).status_code == 200
    paid = await member.post(f"/api/v1/bills/{bill['id']}/payments", {"amount_cents": 200, "note": "venmo'd"})
    assert paid.status_code == 201, paid.text
    assert (await admin.post("/__test/drain", idem=False)).status_code == 200
    assert (await member.post("/api/v1/push/test", idem=False)).status_code == 202
    httpx.post(f"{push}/__control/respond", json={"status": 500, "sub_id": phones["member"]["id"], "times": 1})
    assert (await admin.post(f"/api/v1/payments/{paid.json()['id']}/confirm")).status_code == 200
    assert (await admin.post("/__test/drain", idem=False)).status_code == 200
    received = httpx.get(f"{push}/__test/push", params={"user": f"member-{tag}"}).json()
    assert received, "the flow must really send a Web Push"
    cron = settings.cron_secret
    assert cron is not None
    for auth in (f"Bearer {cron.get_secret_value()}", "Bearer wrong-secret", cron.get_secret_value()):
        await anon.http.get("/api/v1/cron/daily", headers={"Authorization": auth})
    await anon.http.get("/api/test/inbox", headers={"X-Test-Secret": "not-the-secret"})
    codes = await _codes(admin)

    for name, who in (("admin", admin), ("member", member), ("google", googler)):
        session = who.http.cookies.get("kt_session")
        csrf = who.http.cookies.get("kt_csrf")
        assert session and csrf, name
        secrets[f"{name} session token"] = session
        secrets[f"{name} CSRF token"] = csrf
    assert (await member.post("/api/v1/auth/logout", idem=False)).status_code == 204
    assert (await admin.post("/api/v1/auth/logout-all", idem=False)).status_code == 204

    for name, email in emails.items():
        secrets[f"{name} email"] = email
    secrets["phone (E.164)"] = PHONE
    secrets["phone (digits)"] = PHONE[2:]
    for i, code in enumerate(codes):
        secrets[f"one-time code {i}"] = code
    for owner, sub in phones.items():
        secrets[f"{owner} push endpoint"] = sub["endpoint"]
        secrets[f"{owner} push p256dh"] = sub["keys"]["p256dh"]
        secrets[f"{owner} push auth"] = sub["keys"]["auth"]
    secrets["invite token"] = invite
    secrets["bill link token"] = link
    secrets["TEST_INBOX_SECRET"] = TEST_INBOX_SECRET
    secrets["CRON_SECRET"] = cron.get_secret_value()
    secrets["SESSION_SECRET"] = settings.session_secret.get_secret_value()
    secrets["TOKEN_HASH_PEPPER"] = settings.token_hash_pepper.get_secret_value()
    if settings.google_client_secret:
        secrets["GOOGLE_CLIENT_SECRET"] = settings.google_client_secret.get_secret_value()
    secrets["VAPID private key"] = vapid_keys(settings)[1]
    return secrets


def _pattern(value: str) -> re.Pattern[str]:
    """Digits-only secrets (codes, phone digits) must stand alone; anything else matches anywhere,
    as typed or URL-encoded (an address in a query string is still an address)."""
    if value.isdigit():
        return re.compile(rf"(?<!\d){re.escape(value)}(?!\d)")
    forms = {value, quote(value, safe=""), quote_plus(value)}
    return re.compile("|".join(re.escape(f) for f in sorted(forms)))


def _shipped_lines(buf: io.StringIO) -> list[str]:
    """JSON log lines without the timestamp (its microseconds can look like a 6-digit code)."""
    out = []
    for line in buf.getvalue().splitlines():
        record = json.loads(line)
        record.pop("ts", None)
        out.append(json.dumps(record))
    return out


def _raw(record: logging.LogRecord) -> str:
    extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD}
    exc = logging.Formatter().formatException(record.exc_info) if record.exc_info else ""
    return f"{record.getMessage()} {extras!r} {exc}"


_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def _is_test_client_line(record: logging.LogRecord) -> bool:
    """httpx/httpcore also log the requests the *test* makes (to the app, and the browser's hop to
    the mock Google issuer). Those happen outside any app request, so no request id is set."""
    return record.name.startswith(("httpx", "httpcore")) and request_id_var.get() is None


@tid("SEC-07")
async def test_SEC_07_no_secrets_in_logs(
    debug_app: tuple[Any, Callable[[], Api]], push_service: str, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app, make = debug_app
    root = logging.getLogger()
    shipped = io.StringIO()
    handler = logging.StreamHandler(shipped)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(lambda r: not _is_test_client_line(r))
    root.addHandler(handler)
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    try:
        secrets = await run_flow(app, make, push_service, settings)
    finally:
        root.removeHandler(handler)
        root.removeHandler(caplog.handler)
    records = list(caplog.records)
    assert records, "the flow should log something at DEBUG"

    lines = _shipped_lines(shipped)
    problems = []
    for name, value in secrets.items():
        pattern = _pattern(value)
        shipped_hits = [line for line in lines if pattern.search(line)]
        if shipped_hits:
            problems.append(f"{name} in the shipped JSON log: {shipped_hits[0][:240]}")
        app_hits = [r for r in records if r.name.startswith("app") and pattern.search(_raw(r))]
        if app_hits:
            problems.append(f"{name} passed to an app log call ({app_hits[0].name}): {_raw(app_hits[0])[:240]}")
    assert not problems, "\n".join(problems)


@tid("SEC-07")
def test_SEC_07_access_log_lines_lose_link_tokens_codes_and_contacts() -> None:
    """Uvicorn's access log goes through the same JSON formatter (`app.logs.configure_logging`): a
    request line that carries a link token, a code or an address is redacted before it ships."""
    token = "Ab3dE5gH7jK9mN1pQ3sT5v"  # 22 base62 characters, the link-token shape (ADR-0018)
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "203.0.113.9:5000",
            "GET",
            f"/api/v1/b/{token}?code=123456&email=meera@example.com&phone=%2B12125550142",
            "1.1",
            200,
        ),
        None,
    )
    line = JsonFormatter().format(record)
    for secret in (token, "123456", "meera@example.com", "12125550142"):
        assert secret not in line, (secret, line)
    assert redact_text(f"/api/v1/join/{token}") == "/api/v1/join/[redacted]"
