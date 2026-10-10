"""`/__test/*` control routes. Mounted only when APP_ENV=test and TEST_ROUTES_ENABLED=true (SEC-11).

The module is imported only in that case (see `app.main.create_app`), so production builds never
contain these routes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.adapters.extractor import FAKE_EXTRACTOR_MODE_KEY
from app.adapters.sms import FakeSms
from app.clock import REDIS_CLOCK_KEY, ControllableClock

router = APIRouter(prefix="/__test", tags=["test-only"], include_in_schema=False)


class ClockBody(BaseModel):
    now: datetime | None = None
    advance_seconds: float | None = None


class ClockState(BaseModel):
    now: datetime
    overridden: bool


def _clock(request: Request) -> ControllableClock:
    clock: ControllableClock = request.app.state.clock
    return clock


@router.get("/clock", response_model=ClockState)
async def get_clock(request: Request) -> ClockState:
    clock = _clock(request)
    return ClockState(now=clock.now(), overridden=bool(clock.dump()))


@router.post("/clock", response_model=ClockState)
async def set_clock(body: ClockBody, request: Request) -> ClockState:
    from datetime import timedelta

    clock = _clock(request)
    if body.now is not None:
        clock.set(body.now)
    if body.advance_seconds:
        clock.advance(timedelta(seconds=body.advance_seconds))
    if request.app.state.redis is not None:
        await request.app.state.redis.set(REDIS_CLOCK_KEY, clock.dump())
    return ClockState(now=clock.now(), overridden=True)


@router.delete("/clock", status_code=204)
async def reset_clock(request: Request) -> Response:
    _clock(request).reset()
    if request.app.state.redis is not None:
        await request.app.state.redis.delete(REDIS_CLOCK_KEY)
    return Response(status_code=204)


@router.get("/sms")
async def sms(request: Request, to: str | None = None) -> list[dict[str, str]]:
    return await FakeSms(request.app.state.sessions, request.app.state.clock).sent(to)


@router.delete("/sms", status_code=204)
async def clear_sms(request: Request) -> Response:
    from sqlalchemy import delete

    from app.adapters.sms import FAKE_SMS_SUBJECT
    from app.models import TestInboxMessage

    async with request.app.state.sessions() as db, db.begin():
        await db.execute(delete(TestInboxMessage).where(TestInboxMessage.subject == FAKE_SMS_SUBJECT))
    return Response(status_code=204)


class ExtractorMode(BaseModel):
    mode: Literal["normal", "fail", "hang"]


@router.post("/extractor", status_code=204)
async def extractor_mode(body: ExtractorMode, request: Request) -> Response:
    if request.app.state.redis is not None:
        await request.app.state.redis.set(FAKE_EXTRACTOR_MODE_KEY, body.mode)
    return Response(status_code=204)


@router.post("/drain")
async def drain(request: Request) -> dict[str, int]:
    """Run the outbox drain now with the controllable clock (tests and e2e)."""
    from app.services.notify import drain as run

    result = await run(request.app.state.notify)
    return {"outbox": result.outbox, "notifications": result.notifications, "sent": result.sent}


@router.post("/use-soon")
async def use_soon(request: Request) -> dict[str, int]:
    from app.services.notify import use_soon_roundup

    return {"created": await use_soon_roundup(request.app.state.notify)}
