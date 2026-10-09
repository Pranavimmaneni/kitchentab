"""arq worker: `arq app.workers.worker.WorkerSettings`.

Milestone 1 ships the skeleton: settings validation, shared clock, and an outbox poll that only
counts pending rows. Fan-out to push and email (Milestone 9), extraction (5), OG images (4) and
kitchen jobs (8) are added as their milestones land.
"""

from __future__ import annotations

import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.clock import REDIS_CLOCK_KEY, ControllableClock
from app.db import make_engine, make_sessionmaker
from app.logs import configure_logging
from app.models import OutboxRow
from app.settings import get_settings

log = logging.getLogger("app.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url, pool_size=5)
    ctx["engine"] = engine
    ctx["sessions"] = make_sessionmaker(engine)
    ctx["clock"] = ControllableClock()
    ctx["redis_client"] = Redis.from_url(settings.redis_url, decode_responses=True)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["redis_client"].aclose()
    await ctx["engine"].dispose()


async def sync_clock(ctx: dict[str, Any]) -> None:
    """Follow the API's test clock override (TEST_PLAN §2) before time-dependent work."""
    ctx["clock"].load(await ctx["redis_client"].get(REDIS_CLOCK_KEY))


async def poll_outbox(ctx: dict[str, Any]) -> int:
    await sync_clock(ctx)
    async with ctx["sessions"]() as session:
        pending = await session.scalar(select(func.count()).select_from(OutboxRow).where(OutboxRow.status == "pending"))
    if pending:
        log.info("outbox pending", extra={"pending": pending})
    return int(pending or 0)


class WorkerSettings:
    functions = [poll_outbox]
    cron_jobs = [cron(poll_outbox, second={0, 10, 20, 30, 40, 50}, run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
