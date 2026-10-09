"""Google sign-in: OIDC authorization code with PKCE (PRD §7). Shown only when configured.

The transport is injectable so tests run the real code against the mock issuer in-process.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet

from app.errors import ErrorCode, Problem


@dataclass(frozen=True)
class AuthRequest:
    url: str
    state: str
    verifier: str
    nonce: str


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    name: str


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class GoogleOidc:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._transport = transport
        self._discovery: dict[str, Any] | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, transport=self._transport)

    async def discovery(self) -> dict[str, Any]:
        if self._discovery is None:
            async with self._client() as client:
                resp = await client.get(f"{self._issuer}/.well-known/openid-configuration")
                resp.raise_for_status()
                self._discovery = resp.json()
        return self._discovery

    async def start(self, login_hint: str | None = None) -> AuthRequest:
        meta = await self.discovery()
        state, verifier, nonce = (
            secrets.token_urlsafe(24),
            secrets.token_urlsafe(48),
            secrets.token_urlsafe(16),
        )
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if login_hint:
            params["login_hint"] = login_hint
        return AuthRequest(f"{meta['authorization_endpoint']}?{urlencode(params)}", state, verifier, nonce)

    async def finish(self, code: str, verifier: str, nonce: str) -> GoogleIdentity:
        meta = await self.discovery()
        async with self._client() as client:
            token_resp = await client.post(
                meta["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                    "code_verifier": verifier,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            if token_resp.status_code != 200:
                raise Problem(400, ErrorCode.FORBIDDEN, "Google sign-in didn't finish. Try again.")
            jwks = KeySet.import_key_set((await client.get(meta["jwks_uri"])).json())
        id_token = token_resp.json()["id_token"]
        try:
            claims = jwt.decode(id_token, jwks, algorithms=["RS256"]).claims
            jwt.JWTClaimsRegistry(
                iss={"essential": True, "values": [self._issuer, self._issuer.removeprefix("https://")]},
                aud={"essential": True, "value": self._client_id},
                nonce={"essential": True, "value": nonce},
            ).validate(claims)
        except Exception as exc:
            raise Problem(400, ErrorCode.FORBIDDEN, "Google sign-in couldn't be verified") from exc
        if int(claims.get("exp", 0)) < time.time():
            raise Problem(400, ErrorCode.FORBIDDEN, "Google sign-in expired. Try again.")
        if claims.get("email_verified") is not True or not claims.get("email"):
            raise Problem(403, ErrorCode.EMAIL_UNVERIFIED, "That Google account's email isn't verified")
        return GoogleIdentity(str(claims["sub"]), str(claims["email"]).lower(), True, str(claims.get("name", "")))
