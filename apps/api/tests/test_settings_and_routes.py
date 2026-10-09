"""Settings validation at boot (PRD §12), health, and test-only routes (SEC-11)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.settings import Settings, SettingsError
from tests.harness.tags import tid

BASE: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "session_secret": "x" * 40,
    "token_hash_pepper": "y" * 40,
}

REAL_PROD: dict[str, Any] = {
    **BASE,
    "app_env": "prod",
    "email_provider": "resend",
    "resend_api_key": "re_key",
    "sms_provider": "twilio_verify",
    "twilio_account_sid": "AC1",
    "twilio_auth_token": "tok",
    "twilio_verify_service_sid": "VA1",
    "extractor_provider": "vision",
    "extractor_api_key": "k",
    "extractor_model": "m",
    "push_provider": "webpush",
    "vapid_public_key": "pub",
    "vapid_private_key": "priv",
    "google_client_id": "cid",
    "google_client_secret": "secret",
    "turnstile_secret_key": "ts",
}


def build(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**BASE, **overrides})  # type: ignore[call-arg]


def test_missing_secret_fails_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ValueError, match="session_secret"):
        Settings(_env_file=None, database_url="x", redis_url="y", token_hash_pepper="z")  # type: ignore[call-arg]


def test_local_defaults_are_valid() -> None:
    s = build(app_env="local")
    assert s.email_provider == "fake" and not s.test_routes_active


def test_prod_rejects_fakes_and_placeholder_secrets() -> None:
    with pytest.raises(SettingsError) as err:
        build(app_env="prod", session_secret="change-me-64-random-bytes")
    message = str(err.value)
    for expected in ("SESSION_SECRET", "EMAIL_PROVIDER=fake", "SMS_PROVIDER=fake", "PUSH_PROVIDER=fake",
                     "EXTRACTOR_PROVIDER=fake", "Google OIDC", "TURNSTILE_SECRET_KEY"):
        assert expected in message


def test_real_prod_config_boots() -> None:
    s = Settings(_env_file=None, **REAL_PROD)  # type: ignore[call-arg]
    assert s.is_deployed


def test_provider_keys_are_required_when_provider_is_real() -> None:
    with pytest.raises(SettingsError, match="VAPID_PRIVATE_KEY"):
        build(push_provider="webpush", vapid_public_key="pub")


@tid("SEC-11")
@pytest.mark.parametrize("env", ["local", "staging", "prod"])
def test_SEC_11_test_routes_flag_refused_outside_test(env: str) -> None:
    extra = REAL_PROD if env in ("staging", "prod") else {}
    with pytest.raises(SettingsError, match="TEST_ROUTES_ENABLED"):
        Settings(_env_file=None, **{**BASE, **extra, "app_env": env, "test_routes_enabled": True})  # type: ignore[call-arg]


@tid("SEC-11")
async def test_SEC_11_test_routes_absent_without_flag(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"test_routes_enabled": False}))
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/__test")]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as c:
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
    assert [e["code"] for e in (await client.get("/__test/sms", params={"to": "+12015550142"})).json()] == ["246810"]
