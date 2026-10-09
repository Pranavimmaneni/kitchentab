"""Settings validation at boot (PRD §12), health, and test-only routes (SEC-11)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.main import create_app
from app.settings import Settings
from tests.harness.tags import tid

BASE: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "session_secret": "x" * 40,
    "token_hash_pepper": "y" * 40,
}

# Day-one production per ADR-0016: test inbox, no phone, no Google, local extraction.
REAL_PROD: dict[str, Any] = {
    **BASE,
    "app_env": "prod",
    "profile": "vercel",
    "redis_url": None,
    "cron_secret": "c" * 40,
    "email_provider": "testinbox",
    "test_inbox_secret": "t" * 64,
    "extractor_provider": "local",
    "push_provider": "webpush",
    "vapid_public_key": "pub",
    "vapid_private_key": "priv",
    "storage_provider": "vercel_blob",
    "blob_read_write_token": "vercel_blob_rw_x",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest sets the test env process-wide; validation tests need a blank slate."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def build(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**BASE, **overrides})


@pytest.mark.usefixtures("clean_env")
def test_missing_secret_fails_boot() -> None:
    with pytest.raises(ValueError, match="session_secret"):
        Settings(_env_file=None, database_url="x", redis_url="y", token_hash_pepper="z")


@pytest.mark.usefixtures("clean_env")
def test_local_defaults_are_valid() -> None:
    s = build(app_env="local")
    assert s.email_provider == "fake" and not s.test_routes_active


@pytest.mark.usefixtures("clean_env")
def test_prod_rejects_fakes_and_placeholder_secrets() -> None:
    with pytest.raises(ValidationError) as err:
        build(
            app_env="prod",
            session_secret="change-me-64-random-bytes",
            sms_provider="fake",
            extractor_provider="fake",
        )
    message = str(err.value)
    for expected in (
        "SESSION_SECRET",
        "CRON_SECRET",
        "PROFILE must be vercel",
        "EMAIL_PROVIDER=fake",
        "SMS_PROVIDER=fake",
        "PUSH_PROVIDER=fake",
        "EXTRACTOR_PROVIDER=fake",
        "STORAGE_PROVIDER must be vercel_blob",
    ):
        assert expected in message


@pytest.mark.usefixtures("clean_env")
def test_real_prod_config_boots() -> None:
    s = Settings(_env_file=None, **REAL_PROD)
    assert s.is_deployed


@pytest.mark.usefixtures("clean_env")
def test_provider_keys_are_required_when_provider_is_real() -> None:
    with pytest.raises(ValidationError, match="VAPID_PRIVATE_KEY"):
        build(push_provider="webpush", vapid_public_key="pub")


@tid("SEC-11")
@pytest.mark.parametrize("env", ["local", "staging", "prod"])
@pytest.mark.usefixtures("clean_env")
def test_SEC_11_test_routes_flag_refused_outside_test(env: str) -> None:
    extra = REAL_PROD if env in ("staging", "prod") else {}
    with pytest.raises(ValidationError, match="TEST_ROUTES_ENABLED"):
        Settings(_env_file=None, **{**BASE, **extra, "app_env": env, "test_routes_enabled": True})


@tid("SEC-11")
async def test_SEC_11_test_routes_absent_without_flag(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"test_routes_enabled": False}))
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/__test")]
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://api.test") as c,
    ):
        assert (await c.get("/__test/clock")).status_code == 404
        assert (await c.post("/__test/clock", json={"now": "2026-01-01T00:00:00Z"})).status_code == 404


async def test_health_and_readiness(client: httpx.AsyncClient) -> None:
    health = await client.get("/api/v1/health")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert health.headers["x-request-id"]
    ready = await client.get("/api/v1/health/ready")
    assert ready.json() == {"status": "ok", "database": True, "redis": True}


async def test_unknown_route_is_problem_json(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == "NOT_FOUND"


async def test_clock_control_pins_and_advances_server_time(client: httpx.AsyncClient, redis: Any) -> None:
    pinned = datetime(2026, 10, 3, 23, 0, tzinfo=UTC)
    resp = await client.post("/__test/clock", json={"now": pinned.isoformat()})
    assert datetime.fromisoformat(resp.json()["now"]) == pinned
    assert (await client.get("/api/v1/health")).json()["time"] == pinned.isoformat()
    resp = await client.post("/__test/clock", json={"advance_seconds": 11 * 60})
    assert datetime.fromisoformat(resp.json()["now"]) == datetime(2026, 10, 3, 23, 11, tzinfo=UTC)
    assert await redis.get("test:clock:override"), "worker processes follow the override via Redis"
    assert (await client.delete("/__test/clock")).status_code == 204
    assert (await client.get("/__test/clock")).json()["overridden"] is False


async def test_sms_inbox_route(client: httpx.AsyncClient, app: Any) -> None:
    from app.adapters.sms import FakeSms

    await FakeSms(app.state.redis, app.state.clock).send_code("+12015550142", "246810")
    assert [e["code"] for e in (await client.get("/__test/sms", params={"to": "+12015550142"})).json()] == [
        "246810"
    ]


@pytest.mark.usefixtures("clean_env")
def test_test_inbox_needs_a_long_secret_and_smtp_needs_credentials() -> None:
    with pytest.raises(ValidationError, match="TEST_INBOX_SECRET"):
        build(email_provider="testinbox", test_inbox_secret="short")
    assert build(email_provider="testinbox", test_inbox_secret="s" * 64).email_provider == "testinbox"
    with pytest.raises(ValidationError, match="SMTP_PASSWORD"):
        build(email_provider="smtp", smtp_host="smtp.gmail.com", smtp_user="a@gmail.com")


@pytest.mark.usefixtures("clean_env")
def test_vercel_profile_runs_without_redis_and_local_profile_needs_it() -> None:
    s = build(profile="vercel", redis_url=None)
    assert (s.use_redis, s.queue) == (False, "vercel")
    with pytest.raises(ValidationError, match="REDIS_URL"):
        build(profile="local", redis_url=None)


@pytest.mark.usefixtures("clean_env")
def test_phone_and_google_are_off_until_configured() -> None:
    day_one = Settings(_env_file=None, **REAL_PROD)
    assert not day_one.phone_enabled and not day_one.google_enabled
    with pytest.raises(ValidationError, match="TURNSTILE_SECRET_KEY"):
        build(
            sms_provider="twilio_verify",
            twilio_account_sid="AC",
            twilio_auth_token="t",
            twilio_verify_service_sid="VA",
        )
