"""FastAPI application factory: `uvicorn --factory app.main:create_app`."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.adapters.queue import kicker
from app.clock import ControllableClock
from app.db import make_engine, make_read_sessionmaker, make_sessionmaker
from app.deps import BodyLimitMiddleware, NulGuardMiddleware, guard_middleware
from app.errors import install_error_handlers
from app.ids import uuid7
from app.logs import configure_logging, request_id_var
from app.routes import account, auth, bills, events, health, houses, kitchen, lifecycle, me, notify, tabs, test_inbox
from app.routes import receipts as receipt_routes
from app.services.bus import NullBus, RedisBus
from app.services.house_write import IdempotentReplay, WriteContext
from app.services.notify_runtime import build_deps
from app.settings import Settings, get_settings

API_PREFIX = "/api/v1"


API_CSP = "default-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"


def _security_headers(response: Response, settings: Settings) -> None:
    """SEC-03 for API responses. The web app's CSP lives in Next.js (ADR-0007); the API only ever
    serves JSON, SSE and two tiny confirmation pages, so it can deny everything."""
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Content-Security-Policy", API_CSP)
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Cache-Control", "no-store")  # per-user data never sits in shared caches
    h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if settings.is_deployed:
        h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()  # raises ValidationError on bad config: boot fails
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            serverless=settings.profile == "vercel",
            serverless_pool_size=settings.db_serverless_pool_size,
        )
        redis = (
            Redis.from_url(settings.redis_url, decode_responses=True)
            if settings.use_redis and settings.redis_url
            else None
        )
        clock = ControllableClock()
        app.state.settings = settings
        app.state.engine = engine
        app.state.redis = redis
        app.state.clock = clock
        app.state.sessions = make_sessionmaker(engine)
        app.state.read_sessions = make_read_sessionmaker(engine)
        # PROFILE=vercel: no Redis; SSE polls house_events (ADR-0012).
        app.state.kicker = kicker(settings)
        app.state.writes = WriteContext(
            app.state.sessions, clock, RedisBus(redis) if redis else NullBus(), app.state.kicker
        )
        app.state.notify = build_deps(settings, app.state.sessions, clock, app.state.kicker)
        try:
            yield
        finally:
            if redis is not None:
                await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="KitchenTab API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None if settings.is_deployed else f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    install_error_handlers(app)

    # Same origin in every environment (ADR-0006): no CORS. CSRF, body cap: deps.guard_middleware.
    app.middleware("http")(guard_middleware)
    app.add_middleware(NulGuardMiddleware)  # SEC-06: no NUL reaches Postgres
    app.add_middleware(BodyLimitMiddleware)  # outermost: also catches chunked bodies

    @app.exception_handler(IdempotentReplay)
    async def _replay(request: Request, exc: IdempotentReplay) -> JSONResponse:
        return JSONResponse(exc.body, status_code=exc.status_code, headers={"Idempotent-Replayed": "true"})

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("X-Request-Id") or str(uuid7())
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        _security_headers(response, settings)
        response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
        return response

    for router in (
        health.router,
        auth.router,
        me.router,
        account.router,
        houses.router,
        bills.router,
        receipt_routes.router,
        lifecycle.router,
        tabs.router,
        kitchen.router,
        notify.router,
        events.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    app.include_router(test_inbox.router)  # 404 unless EMAIL_PROVIDER=testinbox (ADR-0016)
    if settings.test_routes_active:
        from app.routes import test_routes  # imported only in test env (SEC-11)

        app.include_router(test_routes.router)
    return app
