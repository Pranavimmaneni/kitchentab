"""Fake Web Push service (TEST_PLAN §2). Local dev and tests only.

It plays the role of a browser's push service (FCM, APNs, Mozilla autopush):

* `POST /subscriptions` creates a test subscription: a P-256 key pair and a 16-byte auth secret
  held here, so payloads can be decrypted. Returns the browser-style
  `{endpoint, keys: {p256dh, auth}}` the app stores in `push_subscriptions`.
* `POST /push/{sub_id}` accepts a real Web Push request: verifies the VAPID JWT (RFC 8292, ES256,
  `aud` = this origin, `exp` within 24 h), decrypts the RFC 8291 `aes128gcm` body and stores it.
* `GET /__test/push?user=` lists what each test user received; `DELETE /__test/push` clears it.
* `POST /__control/respond` makes the next pushes answer 201/404/410/429/500 (CHAOS-05, PUSH-05).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import http_ece
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

MAX_RECORD_BYTES = 4096


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class Sub:
    id: str
    user: str
    private_key: ec.EllipticCurvePrivateKey
    auth: bytes

    @property
    def p256dh(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return b64url(raw)


@dataclass
class State:
    base_url: str
    subs: dict[str, Sub] = field(default_factory=dict)
    received: list[dict[str, Any]] = field(default_factory=list)
    # (sub_id or "*", status, remaining count; -1 = until reset)
    forced: list[list[Any]] = field(default_factory=list)


class NewSubscription(BaseModel):
    user: str = Field(min_length=1, max_length=120)


class Respond(BaseModel):
    status: int = Field(ge=200, le=599)
    sub_id: str | None = None
    times: int = -1


def verify_vapid(authorization: str, endpoint_origin: str, now: float) -> dict[str, Any]:
    """Validate `Authorization: vapid t=<jwt>, k=<p256 public key>` and return the claims."""
    scheme, _, params = authorization.partition(" ")
    if scheme.lower() != "vapid":
        raise HTTPException(401, "expected vapid authorization")
    parts = dict(p.strip().split("=", 1) for p in params.split(",") if "=" in p)
    token, key = parts.get("t"), parts.get("k")
    if not token or not key:
        raise HTTPException(401, "vapid t= and k= required")
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(key))
    header_b64, payload_b64, sig_b64 = token.split(".")
    header = json.loads(b64url_decode(header_b64))
    if header.get("alg") != "ES256":
        raise HTTPException(401, "vapid alg must be ES256")
    sig = b64url_decode(sig_b64)
    if len(sig) != 64:
        raise HTTPException(401, "bad signature length")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    try:
        public.verify(der, f"{header_b64}.{payload_b64}".encode(), ec.ECDSA(hashes.SHA256()))
    except Exception as exc:
        raise HTTPException(401, "vapid signature invalid") from exc
    claims: dict[str, Any] = json.loads(b64url_decode(payload_b64))
    if claims.get("aud") != endpoint_origin:
        raise HTTPException(401, f"vapid aud must be {endpoint_origin}")
    exp = int(claims.get("exp", 0))
    if exp <= now or exp > now + 24 * 3600 + 60:
        raise HTTPException(401, "vapid exp must be in the next 24 h")
    if not str(claims.get("sub", "")).startswith(("mailto:", "https:")):
        raise HTTPException(401, "vapid sub must be mailto: or https:")
    claims["_k"] = key
    return claims


def create_app(base_url: str | None = None) -> FastAPI:
    state = State(
        base_url=(base_url or os.environ.get("FAKE_PUSH_BASE_URL", "http://localhost:4010")).rstrip("/")
    )
    app = FastAPI(title="KitchenTab fake push service")
    app.state.fake = state

    def origin() -> str:
        parts = urlsplit(state.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    def take_forced(sub_id: str) -> int | None:
        for rule in state.forced:
            target, status, remaining = rule
            if target in ("*", sub_id) and remaining != 0:
                if remaining > 0:
                    rule[2] -= 1
                return int(status)
        return None

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/subscriptions")
    async def subscribe(body: NewSubscription) -> dict[str, Any]:
        sub = Sub(secrets.token_hex(8), body.user, ec.generate_private_key(ec.SECP256R1()), os.urandom(16))
        state.subs[sub.id] = sub
        return {
            "id": sub.id,
            "endpoint": f"{state.base_url}/push/{sub.id}",
            "keys": {"p256dh": sub.p256dh, "auth": b64url(sub.auth)},
        }

    @app.post("/push/{sub_id}", status_code=201)
    async def push(sub_id: str, request: Request, response: Response) -> Response:
        sub = state.subs.get(sub_id)
        if sub is None:
            raise HTTPException(410, "subscription expired or unknown")
        forced = take_forced(sub_id)
        claims = verify_vapid(request.headers.get("authorization", ""), origin(), time.time())
        if request.headers.get("content-encoding", "").lower() != "aes128gcm":
            raise HTTPException(400, "content-encoding must be aes128gcm")
        ttl = request.headers.get("ttl")
        if ttl is None or not ttl.isdigit():
            raise HTTPException(400, "TTL header required")
        body = await request.body()
        if len(body) > MAX_RECORD_BYTES:
            raise HTTPException(413, "payload too large")
        plaintext = http_ece.decrypt(
            body, private_key=sub.private_key, auth_secret=sub.auth, version="aes128gcm"
        )
        try:
            payload: Any = json.loads(plaintext)
        except ValueError:
            payload = plaintext.decode("utf-8", "replace")
        record = {
            "sub_id": sub_id,
            "user": sub.user,
            "received_at": time.time(),
            "ttl": int(ttl),
            "urgency": request.headers.get("urgency", "normal"),
            "topic": request.headers.get("topic"),
            "encrypted_bytes": len(body),
            "plaintext_bytes": len(plaintext),
            "vapid_sub": claims.get("sub"),
            "vapid_public_key": claims["_k"],
            "payload": payload,
            "answered": forced or 201,
        }
        state.received.append(record)
        if forced is not None and forced != 201:
            headers = {"Retry-After": "1"} if forced == 429 else {}
            return Response(status_code=forced, headers=headers)
        return Response(
            status_code=201, headers={"Location": f"{state.base_url}/messages/{len(state.received)}"}
        )

    @app.get("/__test/push")
    async def received(user: str | None = None) -> list[dict[str, Any]]:
        return [r for r in state.received if user is None or r["user"] == user]

    @app.delete("/__test/push", status_code=204)
    async def clear() -> Response:
        state.received.clear()
        state.forced.clear()
        return Response(status_code=204)

    @app.post("/__control/respond", status_code=204)
    async def respond(body: Respond) -> Response:
        state.forced.insert(0, [body.sub_id or "*", body.status, body.times])
        return Response(status_code=204)

    @app.delete("/__control/respond", status_code=204)
    async def reset_respond() -> Response:
        state.forced.clear()
        return Response(status_code=204)

    return app
