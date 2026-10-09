"""FastAPI application factory: `uvicorn --factory app.main:create_app`."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.clock import ControllableClock
from app.db import make_engine, make_sessionmaker
from app.deps import BodyLimitMiddleware, guard_middleware
from app.errors import install_error_handlers
from app.ids import uuid7
from app.logs import configure_logging, request_id_var
from app.routes import account, auth, bills, events, health, houses, kitchen, lifecycle, me, tabs, test_inbox
from app.services.bus import NullBus, RedisBus
from app.services.house_write import IdempotentReplay, WriteContext
from app.settings import Settings, get_settings

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()  # raises ValidationError on bad config: boot fails
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url, serverless=settings.profile == "vercel")
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
        # PROFILE=vercel: no Redis; SSE polls house_events (ADR-0012).
        app.state.writes = WriteContext(app.state.sessions, clock, RedisBus(redis) if redis else NullBus())
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
        response.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
        return response

    for router in (
        health.router,
        auth.router,
        me.router,
        account.router,
        houses.router,
        bills.router,
        lifecycle.router,
        tabs.router,
        kitchen.router,
        events.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    app.include_router(test_inbox.router)  # 404 unless EMAIL_PROVIDER=testinbox (ADR-0016)
    if settings.test_routes_active:
        from app.routes import test_routes  # imported only in test env (SEC-11)

        app.include_router(test_routes.router)
    return app
