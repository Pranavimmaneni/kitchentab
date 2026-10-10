"""Platform guards: body caps, client version, proxy headers, retention purges, deployed config."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.maintenance import purge_expired
from app.settings import Settings
from tests.harness.api import Api
from tests.harness.tags import tid
from tests.test_settings_and_routes import REAL_PROD


@tid("VERCEL-03")
async def test_VERCEL_03_bodies_over_4_5_mb_are_refused_even_when_chunked(api: Any) -> None:
    a: Api = api()
    await a.sign_in("big@example.com")
    big = b'{"name": "' + b"x" * 4_600_000 + b'"}'
    headers = {"X-CSRF-Token": await a.ensure_csrf(), "Idempotency-Key": "k", "Content-Type": "application/json"}
    sized = await a.http.post("/api/v1/houses", content=big, headers=headers)
    assert sized.status_code == 413

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(0, len(big), 65536):
            yield big[i : i + 65536]

    chunked = await a.http.post("/api/v1/houses", content=chunks(), headers=headers)
    assert chunked.status_code == 413


async def test_stale_client_gets_426(app: Any, client: httpx.AsyncClient) -> None:
    app.state.settings = app.state.settings.model_copy(update={"min_client_version": 5})
    r = await client.get("/api/v1/health", headers={"X-Client-Version": "4"})
    assert r.status_code == 426
    assert r.json()["code"] == "STALE_CLIENT"
    assert (await client.get("/api/v1/health", headers={"X-Client-Version": "5"})).status_code == 200


async def test_forwarded_for_is_ignored_off_vercel(app: Any) -> None:
    from starlette.requests import Request

    from app.deps import client_ip

    scope = {"type": "http", "app": app, "headers": [(b"x-forwarded-for", b"6.6.6.6")], "client": ("10.0.0.1", 1)}
    app.state.settings = app.state.settings.model_copy(update={"profile": "local"})
    assert client_ip(Request(scope)) == "10.0.0.1"
    app.state.settings = app.state.settings.model_copy(update={"profile": "vercel"})
    assert client_ip(Request(scope)) == "6.6.6.6"


@tid("DATA-03")
async def test_DATA_03_retention_purges(sessions: async_sessionmaker[AsyncSession], api: Any) -> None:
    a: Api = api()
    await a.sign_in("purge@example.com")
    await a.create_house()
    async with sessions() as db, db.begin():
        now = (await db.execute(text("SELECT now()"))).scalar_one()
        first = await purge_expired(db, now)
        assert first["house_events"] == 0
        assert first["otp_codes"] == 0
        later = await purge_expired(db, now + timedelta(days=8))
    assert later["otp_codes"] >= 1
    assert later["house_events"] >= 1
    assert later["test_inbox"] >= 1
    assert later["idempotency_keys"] >= 1
    async with sessions() as db, db.begin():
        again = await purge_expired(db, now + timedelta(days=8))
    assert all(v == 0 for v in again.values()), "idempotent"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"web_origin": "http://localhost:3000"}, "WEB_ORIGIN"),
        ({"public_link_base": "http://kitchentab.vercel.app"}, "PUBLIC_LINK_BASE"),
        ({"queue_provider": "arq"}, "QUEUE_PROVIDER"),
        ({"google_client_id": "x", "google_client_secret": "y"}, "GOOGLE_REDIRECT_URI"),
    ],
)
def test_deployed_settings_need_https_origins_and_vercel_queue(override: dict[str, Any], message: str) -> None:
    Settings(_env_file=None, **REAL_PROD)
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **{**REAL_PROD, **override})
