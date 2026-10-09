"""Liveness and readiness."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok"]
    env: str
    time: str


class Readiness(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    redis: bool | None  # None when the profile runs without Redis


@router.get("/health", response_model=Health)
async def health(request: Request) -> Health:
    return Health(
        status="ok", env=request.app.state.settings.app_env, time=request.app.state.clock.now().isoformat()
    )


@router.get("/health/ready", response_model=Readiness)
async def ready(request: Request, response: Response) -> Readiness:
    db_ok = False
    redis_ok: bool | None = None
    try:
        async with request.app.state.engine.connect() as conn:
            db_ok = (await conn.execute(text("SELECT 1"))).scalar() == 1
    except Exception:
        db_ok = False
    if request.app.state.redis is not None:
        try:
            redis_ok = bool(await request.app.state.redis.ping())
        except Exception:
            redis_ok = False
    ok = db_ok and redis_ok is not False
    if not ok:
        response.status_code = 503
    return Readiness(status="ok" if ok else "degraded", database=db_ok, redis=redis_ok)
