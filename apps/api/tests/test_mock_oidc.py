"""Self-test of the mock Google OIDC issuer (TEST_PLAN §2): PKCE code flow and verifiable ID tokens."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet

from fakeservices.mock_oidc import create_app, subject_for

ISSUER = "http://oidc.test"
CLIENT = "kitchentab-local"
REDIRECT = "http://localhost:3000/auth/google/callback"


@pytest.fixture
async def oidc() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ISSUER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as client:
        yield client


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _authorize(client: httpx.AsyncClient, email: str, challenge: str, **extra: str) -> str:
    params = {
        "client_id": CLIENT,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "openid email profile",
        "state": "st-1",
        "nonce": "n-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": email,
        **extra,
    }
    resp = await client.get("/authorize", params=params)
    assert resp.status_code == 302
    query = parse_qs(urlsplit(resp.headers["location"]).query)
    assert query["state"] == ["st-1"]
    return query["code"][0]


async def test_code_flow_issues_a_verifiable_id_token(oidc: httpx.AsyncClient) -> None:
    discovery = (await oidc.get("/.well-known/openid-configuration")).json()
    assert discovery["issuer"] == ISSUER and "S256" in discovery["code_challenge_methods_supported"]
    verifier, challenge = _pkce()
    code = await _authorize(oidc, "meera@example.test", challenge)
    tokens = (
        await oidc.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
                "client_id": CLIENT,
            },
        )
    ).json()
    jwks = KeySet.import_key_set((await oidc.get("/jwks")).json())
    claims = jwt.decode(tokens["id_token"], jwks, algorithms=["RS256"]).claims
    jwt.JWTClaimsRegistry(iss={"essential": True, "value": ISSUER}, aud={"essential": True, "value": CLIENT}).validate(
        claims
    )
    assert claims["iss"] == ISSUER and claims["aud"] == CLIENT and claims["nonce"] == "n-1"
    assert claims["email"] == "meera@example.test" and claims["email_verified"] is True
    assert claims["sub"] == subject_for("meera@example.test")
    info = (await oidc.get("/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"})).json()
    assert info["email"] == "meera@example.test"


async def test_wrong_pkce_verifier_is_rejected(oidc: httpx.AsyncClient) -> None:
    _, challenge = _pkce()
    code = await _authorize(oidc, "a@example.test", challenge)
    resp = await oidc.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": "not-the-verifier",
            "client_id": CLIENT,
        },
    )
    assert resp.status_code == 400 and "PKCE" in resp.text


async def test_code_is_single_use(oidc: httpx.AsyncClient) -> None:
    verifier, challenge = _pkce()
    code = await _authorize(oidc, "a@example.test", challenge)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
        "client_id": CLIENT,
    }
    assert (await oidc.post("/token", data=form)).status_code == 200
    assert (await oidc.post("/token", data=form)).status_code == 400


async def test_unverified_email_can_be_requested(oidc: httpx.AsyncClient) -> None:
    verifier, challenge = _pkce()
    code = await _authorize(oidc, "u@example.test", challenge, email_verified="false")
    tokens = (
        await oidc.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
                "client_id": CLIENT,
            },
        )
    ).json()
    claims = jwt.decode(tokens["id_token"], KeySet.import_key_set((await oidc.get("/jwks")).json())).claims
    assert claims["email_verified"] is False


async def test_pkce_is_required(oidc: httpx.AsyncClient) -> None:
    resp = await oidc.get(
        "/authorize",
        params={
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "login_hint": "a@example.test",
        },
    )
    assert resp.status_code == 400
