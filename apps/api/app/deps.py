"""Request plumbing shared by every route: context, sign-in, CSRF, body cap, idempotency keys.

Authorization decisions are not made here; they live in `app.policy` (CLAUDE.md rule 2).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import ratelimit
from app.clock import ControllableClock
from app.errors import PROBLEM_JSON, ErrorCode, Problem
from app.models import Session, User
from app.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, csrf_matches, session_hash
from app.services.house_write import Idempotency, WriteContext
from app.settings import Settings

SESSION_TTL = timedelta(days=30)
SESSION_REFRESH_AFTER = timedelta(hours=1)
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# Endpoints with their own authentication (bearer secrets), exempt from CSRF.
# Bearer-secret, signed-link and test endpoints: no browser session to forge.
CSRF_EXEMPT_PREFIXES = ("/api/v1/cron/", "/api/v1/queue/", "/api/v1/email/unsubscribe", "/api/test/", "/__test/")


@dataclass
class Ctx:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    clock: ControllableClock
    writes: WriteContext
    redis: Redis | None
    read_sessions: async_sessionmaker[AsyncSession]  # one snapshot per view (GET handlers)


def ctx(request: Request) -> Ctx:
    s = request.app.state
    return Ctx(s.settings, s.sessions, s.clock, s.writes, s.redis, s.read_sessions)


@dataclass(frozen=True)
class Actor:
    user: User
    session_id: uuid.UUID


# ---- sessions --------------------------------------------------------------------------------


async def optional_actor(request: Request) -> Actor | None:
    if hasattr(request.state, "actor"):
        cached: Actor | None = request.state.actor
        return cached
    token = request.cookies.get(SESSION_COOKIE)
    actor: Actor | None = None
    if token:
        c = ctx(request)
        now = c.clock.now()
        async with c.sessions() as db, db.begin():
            row = (
                await db.execute(
                    select(Session, User)
                    .join(User, User.id == Session.user_id)
                    .where(
                        Session.token_hash == session_hash(token),
                        Session.revoked_at.is_(None),
                        Session.expires_at > now,
                        User.deleted_at.is_(None),
                    )
                )
            ).first()
            if row is not None:
                sess, user = row
                if now - sess.last_seen_at > SESSION_REFRESH_AFTER:  # sliding expiry (AUTH-08)
                    await db.execute(
                        update(Session)
                        .where(Session.id == sess.id)
                        .values(last_seen_at=now, expires_at=now + SESSION_TTL)
                    )
                actor = Actor(user, sess.id)
    request.state.actor = actor
    return actor


async def require_actor(request: Request) -> Actor:
    actor = await optional_actor(request)
    if actor is None:
        raise Problem(401, ErrorCode.UNAUTHENTICATED, "Sign in to do that")
    settings: Settings = request.app.state.settings
    limit = settings.rate_limit_writes_per_min
    if request.method in UNSAFE and limit and not getattr(request.state, "write_counted", False):
        request.state.write_counted = True  # SEC-05: 60 writes/min per user, across devices
        await rate_limit(request, "writes", str(actor.user.id), limit=limit, window=timedelta(minutes=1))
    return actor


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=cookie_secure(settings),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=cookie_secure(settings), httponly=True, samesite="lax")


def cookie_secure(settings: Settings) -> bool:
    return settings.web_origin.startswith("https://")


# ---- rate limits ---------------------------------------------------------------------------


async def rate_limit(request: Request, bucket: str, raw_key: str, *, limit: int, window: timedelta) -> int:
    """Count an attempt in its own transaction so a later failure can't roll it back."""
    c = ctx(request)
    key = hashlib.sha256(f"{bucket}:{raw_key}".encode()).hexdigest()
    async with c.sessions() as db, db.begin():
        return await ratelimit.hit(db, c.clock, bucket, key, limit=limit, window=window)


def client_ip(request: Request) -> str:
    """Behind Vercel's proxy (PROFILE=vercel) the platform sets X-Forwarded-For; anywhere else the
    header is client-controlled, so it is ignored (rate limits key on the IP)."""
    settings: Settings = request.app.state.settings
    if settings.profile == "vercel":
        forwarded = request.headers.get("x-vercel-forwarded-for") or request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---- CSRF, body size, client version (middleware) ----------------------------------------------


def _allowed_origins(settings: Settings) -> set[str]:
    out = set()
    for url in (settings.web_origin, settings.public_link_base):
        parts = urlsplit(url)
        out.add(f"{parts.scheme}://{parts.netloc}")
    return out


def problem_response(problem: Problem, path: str) -> JSONResponse:
    return JSONResponse(
        problem.body(path), status_code=problem.status, media_type=PROBLEM_JSON, headers=problem.headers
    )


async def guard_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    settings: Settings = request.app.state.settings
    path = request.url.path
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.max_body_bytes:  # VERCEL-03
        return problem_response(
            Problem(413, ErrorCode.VALIDATION_FAILED, "That's too big to send", "Upload files directly instead."),
            path,
        )
    version = request.headers.get("x-client-version")
    if (
        version is not None
        and settings.min_client_version
        and (not version.isdigit() or int(version) < settings.min_client_version)
    ):
        return problem_response(
            Problem(426, ErrorCode.STALE_CLIENT, "A new version is ready", "Refresh to keep going."), path
        )
    reads = settings.rate_limit_reads_per_min
    if reads and request.method in ("GET", "HEAD") and path.startswith("/api/v1/") and not path.endswith("/events"):
        try:  # SEC-05: 300 reads/min per IP (the SSE stream is one long read and is not counted)
            await rate_limit(request, "reads", client_ip(request), limit=reads, window=timedelta(minutes=1))
        except Problem as exc:
            return problem_response(exc, path)
    if request.method in UNSAFE and not path.startswith(CSRF_EXEMPT_PREFIXES):
        origin = request.headers.get("origin")
        if origin is not None and origin not in _allowed_origins(settings):
            return problem_response(Problem(403, ErrorCode.FORBIDDEN, "Cross-site request blocked"), path)
        if not csrf_matches(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)):
            return problem_response(Problem(403, ErrorCode.FORBIDDEN, "Missing or stale security token"), path)
    return await call_next(request)


# ---- idempotency ---------------------------------------------------------------------------


def request_hash(request: Request, body: Any) -> str:
    payload = json.dumps(body, sort_keys=True, default=str) if body is not None else ""
    return hashlib.sha256(f"{request.method} {request.url.path}\n{payload}".encode()).hexdigest()


def idempotency(request: Request, actor: Actor, body: Any) -> Idempotency:
    """Every POST that creates or transitions needs a key (400 without one, PRD §21.8)."""
    key = request.headers.get("Idempotency-Key")
    if not key or len(key) > 200:
        raise Problem(400, ErrorCode.IDEMPOTENCY_KEY_REQUIRED, "Idempotency-Key header required")
    return Idempotency(actor.user.id, key, request_hash(request, body))


def if_match(request: Request) -> int:
    """Guarded edits need If-Match with the row version (428 without it)."""
    raw = request.headers.get("If-Match")
    if raw is None:
        raise Problem(428, ErrorCode.IF_MATCH_REQUIRED, "If-Match header required")
    try:
        return int(raw.strip('W/"'))
    except ValueError as exc:
        raise Problem(400, ErrorCode.VALIDATION_FAILED, "If-Match must be a version number") from exc


def client_mutation_id(request: Request) -> str | None:
    value = request.headers.get("X-Client-Mutation-Id")
    return value[:100] if value else None


def now(request: Request) -> datetime:
    return ctx(request).clock.now()


class BodyLimitMiddleware:
    """Bodies without Content-Length (chunked) are buffered here up to the 4.5 MB Vercel limit and
    refused with 413 beyond it, before the app sees them (VERCEL-03). Sized bodies are checked by
    `guard_middleware` from the header. Receipts never come through here (ADR-0015)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        headers = dict(scope.get("headers") or []) if scope["type"] == "http" else {}
        if scope["type"] != "http" or b"content-length" in headers or scope.get("method") in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return
        limit = scope["app"].state.settings.max_body_bytes
        messages: list[dict[str, Any]] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > limit:
                response = problem_response(
                    Problem(
                        413, ErrorCode.VALIDATION_FAILED, "That's too big to send", "Upload files directly instead."
                    ),
                    scope.get("path", ""),
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body"):
                break
        pending = iter(messages)

        async def replay() -> Any:
            return next(pending, None) or await receive()

        await self.app(scope, replay, send)


class NulGuardMiddleware:
    """SEC-06: Postgres text can't hold NUL (U+0000), so a request carrying one in its query, path or
    JSON body is refused with 422 here, before any handler or query sees it."""

    def __init__(self, app: Any) -> None:
        self.app = app

    @staticmethod
    def _refuse(scope: dict[str, Any]) -> Any:
        return problem_response(
            Problem(422, ErrorCode.VALIDATION_FAILED, "Some text has an invisible character we can't store"),
            scope.get("path", ""),
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_path = scope.get("raw_path") or b""
        query = scope.get("query_string") or b""
        if b"%00" in query or b"%00" in raw_path or b"\x00" in query or "\x00" in scope.get("path", ""):
            await self._refuse(scope)(scope, receive, send)
            return
        if scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        messages: list[dict[str, Any]] = []
        body = b""
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        if rb"\u0000" in body or b"\x00" in body:
            await self._refuse(scope)(scope, receive, send)
            return
        pending = iter(messages)

        async def replay() -> Any:
            return next(pending, None) or await receive()

        await self.app(scope, replay, send)
