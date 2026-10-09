"""Mock Google OIDC issuer (TEST_PLAN §2). Local dev and tests only.

Authorization code flow with PKCE (S256), signing RS256 ID tokens for any email:

* `GET /authorize?...&login_hint=<email>` approves immediately (for Playwright); without a hint it
  shows a one-field form. `email_verified=false` can be requested to test AUTH-03 rejection.
* `POST /token` checks the code, redirect URI, client id and PKCE verifier, then returns an
  `id_token` with iss, aud, sub, email, email_verified, name, nonce, iat, exp.
* `/.well-known/openid-configuration`, `/jwks`, `/userinfo` behave like Google's.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

KID = "mock-oidc-1"


def subject_for(email: str) -> str:
    """Stable numeric subject per email, like Google's."""
    return str(int(hashlib.sha256(email.lower().encode()).hexdigest()[:15], 16))


@dataclass
class Grant:
    client_id: str
    redirect_uri: str
    code_challenge: str
    nonce: str | None
    email: str
    email_verified: bool
    name: str
    issued: float


@dataclass
class State:
    issuer: str
    key: RSAKey
    codes: dict[str, Grant] = field(default_factory=dict)
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)


def create_app(issuer: str | None = None) -> FastAPI:
    issuer = (issuer or os.environ.get("MOCK_OIDC_ISSUER", "http://localhost:4011")).rstrip("/")
    key = RSAKey.generate_key(2048, parameters={"kid": KID, "use": "sig", "alg": "RS256"}, private=True)
    state = State(issuer=issuer, key=key)
    app = FastAPI(title="KitchenTab mock Google OIDC")
    app.state.oidc = state

    @app.get("/.well-known/openid-configuration")
    async def discovery() -> dict[str, Any]:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "userinfo_endpoint": f"{issuer}/userinfo",
            "jwks_uri": f"{issuer}/jwks",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "claims_supported": ["sub", "email", "email_verified", "name", "iss", "aud", "nonce"],
        }

    @app.get("/jwks")
    async def jwks() -> dict[str, Any]:
        return {"keys": [state.key.as_dict(private=False)]}

    def issue_code(params: dict[str, str], email: str, verified: bool, name: str) -> RedirectResponse:
        if params.get("response_type") != "code":
            raise HTTPException(400, "response_type must be code")
        if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
            raise HTTPException(400, "PKCE S256 is required")
        code = secrets.token_urlsafe(24)
        state.codes[code] = Grant(
            params["client_id"], params["redirect_uri"], params["code_challenge"], params.get("nonce"),
            email, verified, name or email.split("@")[0].title(), time.time(),
        )
        query = {"code": code}
        if params.get("state"):
            query["state"] = params["state"]
        return RedirectResponse(f"{params['redirect_uri']}?{urlencode(query)}", status_code=302)

    @app.get("/authorize", response_model=None)
    async def authorize(
        request: Request,
        client_id: str,
        redirect_uri: str,
        response_type: str = "code",
        login_hint: str | None = None,
        email_verified: bool = Query(True),
        name: str = "",
    ) -> HTMLResponse | RedirectResponse:
        params = dict(request.query_params)
        if login_hint:
            return issue_code(params, login_hint, email_verified, name)
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in params.items()
        )
        return HTMLResponse(
            "<!doctype html><title>Mock Google sign-in</title><form method=post action=/authorize>"
            f"{hidden}<label>Email <input name=email type=email required autofocus></label>"
            "<label><input name=email_verified type=checkbox value=true checked> verified</label>"
            "<button>Continue</button></form>"
        )

    async def read_form(request: Request) -> dict[str, str]:
        # Parsed by hand: the OAuth endpoints are form-encoded and python-multipart is not a dependency.
        body = (await request.body()).decode()
        return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}

    @app.post("/authorize")
    async def authorize_form(request: Request) -> RedirectResponse:
        form = await read_form(request)
        email = form.pop("email")
        verified = form.pop("email_verified", "false") == "true"
        return issue_code(form, email, verified, form.get("name", ""))

    @app.post("/token")
    async def token(request: Request) -> dict[str, Any]:
        form = await read_form(request)
        grant_type, code = form.get("grant_type"), form.get("code", "")
        redirect_uri, code_verifier = form.get("redirect_uri"), form.get("code_verifier", "")
        client_id = form.get("client_id")
        if grant_type != "authorization_code":
            raise HTTPException(400, "unsupported_grant_type")
        if client_id is None:  # client_secret_basic
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("basic "):
                client_id = base64.b64decode(auth[6:]).decode().split(":", 1)[0]
        grant = state.codes.pop(code, None)
        if grant is None or time.time() - grant.issued > 600:
            raise HTTPException(400, "invalid_grant")
        if grant.redirect_uri != redirect_uri or grant.client_id != client_id:
            raise HTTPException(400, "invalid_grant: redirect_uri or client_id mismatch")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        if challenge != grant.code_challenge:
            raise HTTPException(400, "invalid_grant: PKCE verifier mismatch")
        now = int(time.time())
        claims = {
            "iss": issuer, "aud": grant.client_id, "azp": grant.client_id, "sub": subject_for(grant.email),
            "email": grant.email, "email_verified": grant.email_verified, "name": grant.name,
            "iat": now, "exp": now + 3600,
        }
        if grant.nonce:
            claims["nonce"] = grant.nonce
        id_token = jwt.encode({"alg": "RS256", "kid": KID}, claims, state.key)
        access_token = secrets.token_urlsafe(24)
        state.tokens[access_token] = claims
        return {"access_token": access_token, "token_type": "Bearer", "expires_in": 3600, "id_token": id_token,
                "scope": "openid email profile"}

    @app.get("/userinfo")
    async def userinfo(request: Request) -> dict[str, Any]:
        auth = request.headers.get("authorization", "")
        claims = state.tokens.get(auth.removeprefix("Bearer ").strip())
        if claims is None:
            raise HTTPException(401, "invalid_token")
        return {k: claims[k] for k in ("sub", "email", "email_verified", "name")}

    return app
