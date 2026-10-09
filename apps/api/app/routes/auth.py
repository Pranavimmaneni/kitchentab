"""Sign-in endpoints (PRD §7, §11; ADR-0016). Not house writes, so no house events."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import update

from app.adapters import turnstile
from app.adapters.email import Email, email_sender
from app.adapters.google import GoogleOidc
from app.adapters.sms import sms_verifier
from app.deps import (
    Actor,
    Ctx,
    clear_session_cookie,
    client_ip,
    cookie_secure,
    ctx,
    optional_actor,
    rate_limit,
    require_actor,
    set_session_cookie,
)
from app.errors import ErrorCode, Problem
from app.models import Session
from app.security import (
    CSRF_COOKIE,
    mask_destination,
    new_csrf_token,
    new_otp_code,
    normalize_email,
    normalize_us_phone,
    peppered,
)
from app.services import auth as svc
from app.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])
GOOGLE_COOKIE = "kt_google"


class Methods(BaseModel):
    email: bool
    phone: bool
    google: bool
    turnstile_site_key: str | None


@router.get("/methods", response_model=Methods)
async def methods(request: Request) -> Methods:
    s: Settings = ctx(request).settings
    return Methods(email=True, phone=s.phone_enabled, google=s.google_enabled, turnstile_site_key=s.turnstile_site_key)


class CsrfOut(BaseModel):
    token: str


@router.get("/csrf", response_model=CsrfOut)
async def csrf(request: Request, response: Response) -> CsrfOut:
    """Issue the double-submit token (readable cookie + echoed header on writes; ADR-0006)."""
    token = request.cookies.get(CSRF_COOKIE) or new_csrf_token()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=60 * 60 * 24 * 30,
        secure=cookie_secure(ctx(request).settings),
        samesite="lax",
    )
    return CsrfOut(token=token)


class SendCode(BaseModel):
    channel: Literal["email", "phone"]
    destination: str = Field(min_length=3, max_length=254)
    turnstile_token: str | None = None


class SendCodeOut(BaseModel):
    sent: bool
    masked: str
    resend_after_seconds: int


def _normalize(channel: str, raw: str) -> str:
    value = normalize_email(raw) if channel == "email" else normalize_us_phone(raw)
    if value is None:
        field = "email" if channel == "email" else "US phone number"
        raise Problem(422, ErrorCode.VALIDATION_FAILED, f"That doesn't look like a valid {field}.")
    return value


@router.post("/otp/send", response_model=SendCodeOut)
async def send_code(body: SendCode, request: Request) -> SendCodeOut:
    c = ctx(request)
    pepper = c.settings.token_hash_pepper.get_secret_value()
    destination = _normalize(body.channel, body.destination)
    now = c.clock.now()
    if body.channel == "phone":
        verifier = sms_verifier(c.settings, c.sessions, c.clock)
        if verifier is None:
            raise Problem(404, ErrorCode.METHOD_DISABLED, "Phone codes aren't available yet. Use your email.")
        ip = client_ip(request)
        sent_so_far = await _peek(request, "otp_phone", destination, timedelta(hours=1))
        if sent_so_far >= 2 and not await turnstile.verify(c.settings, body.turnstile_token, ip):
            raise Problem(403, ErrorCode.CAPTCHA_REQUIRED, "Quick check that you're a person", extra={"captcha": True})
        await rate_limit(request, "otp_phone", destination, limit=3, window=timedelta(hours=1))
        await rate_limit(request, "otp_phone_ip", ip, limit=10, window=timedelta(days=1))
        await verifier.start(destination)
        async with c.sessions() as db, db.begin():
            await svc.store_code(db, "phone", destination, None, pepper, now)
    else:
        await rate_limit(request, "otp_email", destination, limit=5, window=timedelta(hours=1))
        code = new_otp_code()
        async with c.sessions() as db, db.begin():
            await svc.store_code(db, "email", destination, code, pepper, now)
        await email_sender(c.settings, c.sessions, c.clock).send(
            Email(
                to=destination,
                subject=f"{code} is your KitchenTab code",
                text=f"Your KitchenTab code is {code}. It works for 10 minutes.\n\n"
                "If you didn't ask for it, you can ignore this email.",
            )
        )
    return SendCodeOut(sent=True, masked=mask_destination(body.channel, destination), resend_after_seconds=30)


async def _peek(request: Request, bucket: str, raw_key: str, window: timedelta) -> int:
    from app import ratelimit

    c = ctx(request)
    key = hashlib.sha256(f"{bucket}:{raw_key}".encode()).hexdigest()
    async with c.sessions() as db:
        return await ratelimit.count(db, c.clock, bucket, key, window=window)


class VerifyCode(BaseModel):
    channel: Literal["email", "phone"]
    destination: str = Field(min_length=3, max_length=254)
    code: str = Field(pattern=r"^\d{6}$")


class SignedIn(BaseModel):
    user_id: str
    display_name: str
    is_new: bool


@router.post("/otp/verify", response_model=SignedIn)
async def verify_code(body: VerifyCode, request: Request, response: Response) -> SignedIn:
    c = ctx(request)
    pepper = c.settings.token_hash_pepper.get_secret_value()
    destination = _normalize(body.channel, body.destination)
    now = c.clock.now()

    # Check and count the attempt in its own transaction so a wrong code is never rolled back.
    async with c.sessions() as db, db.begin():
        row = await svc.latest_code(db, body.channel, destination, pepper)
        problem = svc.code_problem(row, now)
        if problem is not None:
            raise problem
        assert row is not None
        if body.channel == "phone":
            verifier = sms_verifier(c.settings, c.sessions, c.clock)
            if verifier is None:
                raise Problem(404, ErrorCode.METHOD_DISABLED, "Phone codes aren't available yet.")
            ok = await verifier.check(destination, body.code)
        else:
            ok = svc.code_matches(row, body.code, pepper)
        if ok:
            row.consumed_at = now
        else:
            row.attempts += 1
        attempts = row.attempts
    if not ok:
        left = max(0, svc.MAX_ATTEMPTS - attempts)
        if left == 0:
            raise Problem(400, ErrorCode.CODE_LOCKED, "Too many tries. Get a new code.")
        raise Problem(
            400,
            ErrorCode.CODE_MISMATCH,
            f"That code didn't match. {left} tries left.",
            extra={"tries_left": left},
        )

    return await _sign_in(
        c,
        request,
        response,
        provider=body.channel,
        subject=destination,
        email=destination if body.channel == "email" else None,
        phone=destination if body.channel == "phone" else None,
        name=svc.default_name(destination if body.channel == "email" else None),
    )


async def _sign_in(
    c: Ctx,
    request: Request,
    response: Response,
    *,
    provider: Literal["email", "phone", "google"],
    subject: str,
    email: str | None,
    phone: str | None,
    name: str,
) -> SignedIn:
    now = c.clock.now()
    pepper = c.settings.token_hash_pepper.get_secret_value()
    async with c.sessions() as db, db.begin():
        before = await db.scalar(_identity_exists(provider, subject))
        user = await svc.resolve_user(db, provider, subject, email=email, phone=phone, display_name=name, now=now)
        # Signing in replaces any session this browser had (SEC-12: new id every sign-in).
        old = request.cookies.get("kt_session")
        if old:
            from app.security import session_hash

            await db.execute(update(Session).where(Session.token_hash == session_hash(old)).values(revoked_at=now))
        token = await svc.start_session(
            db,
            user,
            now,
            user_agent=request.headers.get("user-agent"),
            ip_hash=peppered(client_ip(request), pepper),
        )
    set_session_cookie(response, c.settings, token)
    response.set_cookie(
        CSRF_COOKIE,
        new_csrf_token(),
        max_age=60 * 60 * 24 * 30,
        secure=cookie_secure(c.settings),
        samesite="lax",
    )
    return SignedIn(user_id=str(user.id), display_name=user.display_name, is_new=not before)


def _identity_exists(provider: str, subject: str):  # type: ignore[no-untyped-def]
    from sqlalchemy import exists, select

    from app.models import AuthIdentity

    return select(exists().where(AuthIdentity.provider == provider, AuthIdentity.provider_subject == subject))


# ---- Google ---------------------------------------------------------------------------------


def google_client(request: Request) -> GoogleOidc:
    s = ctx(request).settings
    if not s.google_enabled or s.google_client_secret is None or s.google_client_id is None:
        raise Problem(404, ErrorCode.METHOD_DISABLED, "Google sign-in isn't set up yet. Use your email.")
    transport = getattr(request.app.state, "google_transport", None)
    return GoogleOidc(
        s.google_issuer,
        s.google_client_id,
        s.google_client_secret.get_secret_value(),
        s.google_redirect_uri,
        transport,
    )


def _sign(payload: dict[str, str], secret: str) -> str:
    body = json.dumps(payload, sort_keys=True)
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{mac}.{body}"


def _unsign(value: str | None, secret: str) -> dict[str, str] | None:
    if not value or "." not in value:
        return None
    mac, body = value.split(".", 1)
    if not hmac.compare_digest(mac, hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    data: dict[str, str] = json.loads(body)
    return data if float(data.get("exp", "0")) > time.time() else None


def _safe_return(path: str | None) -> str:
    return path if path and path.startswith("/") and not path.startswith("//") else "/"


@router.get("/google/start")
async def google_start(request: Request, return_to: str = "/", login_hint: str | None = None) -> RedirectResponse:
    oidc = google_client(request)
    auth = await oidc.start(login_hint)
    secret = ctx(request).settings.session_secret.get_secret_value()
    cookie = _sign(
        {
            "state": auth.state,
            "verifier": auth.verifier,
            "nonce": auth.nonce,
            "return_to": _safe_return(return_to),
            "exp": str(time.time() + 600),
        },
        secret,
    )
    resp = RedirectResponse(auth.url, status_code=302)
    resp.set_cookie(
        GOOGLE_COOKIE,
        cookie,
        max_age=600,
        httponly=True,
        secure=cookie_secure(ctx(request).settings),
        samesite="lax",
        path="/api/v1/auth/google",
    )
    return resp


@router.get("/google/callback")
async def google_callback(request: Request, code: str, state: str) -> RedirectResponse:
    c = ctx(request)
    data = _unsign(request.cookies.get(GOOGLE_COOKIE), c.settings.session_secret.get_secret_value())
    if data is None or not hmac.compare_digest(data["state"], state):
        raise Problem(400, ErrorCode.FORBIDDEN, "Google sign-in expired. Try again.")
    identity = await google_client(request).finish(code, data["verifier"], data["nonce"])
    resp = RedirectResponse(data["return_to"], status_code=302)
    await _sign_in(
        c,
        request,
        resp,
        provider="google",
        subject=identity.subject,
        email=identity.email,
        phone=None,
        name=identity.name or svc.default_name(identity.email),
    )
    resp.delete_cookie(GOOGLE_COOKIE, path="/api/v1/auth/google")
    return resp


# ---- sign out ----------------------------------------------------------------------------------


@router.post("/logout", status_code=204)
async def logout(
    request: Request, response: Response, actor: Annotated[Actor | None, Depends(optional_actor)]
) -> Response:
    c = ctx(request)
    if actor is not None:
        async with c.sessions() as db, db.begin():
            await db.execute(update(Session).where(Session.id == actor.session_id).values(revoked_at=c.clock.now()))
    clear_session_cookie(response, c.settings)
    response.status_code = 204
    return response


@router.post("/logout-all", status_code=204)
async def logout_all(request: Request, response: Response, actor: Annotated[Actor, Depends(require_actor)]) -> Response:
    """Sign out everywhere: every session of this user is revoked (AUTH-09)."""
    c = ctx(request)
    async with c.sessions() as db, db.begin():
        await db.execute(
            update(Session)
            .where(Session.user_id == actor.user.id, Session.revoked_at.is_(None))
            .values(revoked_at=c.clock.now())
        )
    clear_session_cookie(response, c.settings)
    response.status_code = 204
    return response
