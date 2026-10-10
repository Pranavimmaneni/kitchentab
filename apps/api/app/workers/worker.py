"""arq worker: `arq app.workers.worker.WorkerSettings`.

PROFILE=local only (ADR-0017). Drains the outbox every 10 s and when kicked after a commit, and
runs the daily job (the Vercel cron's twin). PROFILE=vercel uses app/queues/outbox.py instead.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.adapters.storage import Storage, StorageError, storage_from_settings
from app.clock import REDIS_CLOCK_KEY, ControllableClock
from app.db import make_engine, make_sessionmaker
from app.logs import configure_logging
from app.services.backup import export_to_storage
from app.services.bill_life import auto_remind
from app.services.bus import RedisBus
from app.services.house_write import WriteContext
from app.services.inventory import expire_requests
from app.services.maintenance import purge_expired
from app.services.notify import drain, use_soon_roundup, weekly_digest
from app.services.notify_runtime import build_deps
from app.settings import get_settings

log = logging.getLogger("app.worker")


def _redis_url() -> str:
    """The arq worker exists only with PROFILE=local, where settings guarantee REDIS_URL."""
    url = get_settings().redis_url
    if not url:
        raise RuntimeError("the arq worker needs REDIS_URL (PROFILE=local)")
    return url


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url, pool_size=5)
    ctx["engine"] = engine
    ctx["sessions"] = make_sessionmaker(engine)
    ctx["clock"] = ControllableClock()
    ctx["redis_client"] = Redis.from_url(_redis_url(), decode_responses=True)
    ctx["notify"] = build_deps(settings, ctx["sessions"], ctx["clock"], None)  # the poll is the wake-up
    ctx["writes"] = WriteContext(ctx["sessions"], ctx["clock"], RedisBus(ctx["redis_client"]))


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["redis_client"].aclose()
    await ctx["engine"].dispose()


async def sync_clock(ctx: dict[str, Any]) -> None:
    """Follow the API's test clock override (TEST_PLAN §2) before time-dependent work."""
    ctx["clock"].load(await ctx["redis_client"].get(REDIS_CLOCK_KEY))


async def drain_job(ctx: dict[str, Any]) -> int:
    """Kicked after each commit with outbox rows (ArqKicker) and polled every 10 s."""
    await sync_clock(ctx)
    result = await drain(ctx["notify"])
    if result.sent:
        log.info("notifications sent", extra={"sent": result.sent})
    return result.sent


def _storage() -> Storage | None:
    try:
        return storage_from_settings(get_settings())
    except StorageError:
        log.warning("receipt storage not configured; receipt files are not purged this run")
        return None


async def daily_job(ctx: dict[str, Any]) -> int:
    """Same work as the Vercel daily cron: expire asks, purge, schedule the 9:00 round-ups, export."""
    await sync_clock(ctx)
    now = ctx["clock"].now()
    storage = _storage()
    await expire_requests(ctx["writes"], now)
    async with ctx["sessions"]() as db, db.begin():
        await purge_expired(db, now, storage)
    created = await use_soon_roundup(ctx["notify"])
    await weekly_digest(ctx["notify"])
    await auto_remind(ctx["writes"], now)
    if storage is not None:
        await export_to_storage(ctx["sessions"], storage, now)
    return created


class WorkerSettings:
    functions: ClassVar[list[Any]] = [drain_job, daily_job]
    cron_jobs: ClassVar[list[Any]] = [
        cron(drain_job, second={0, 10, 20, 30, 40, 50}, run_at_startup=True, unique=True),
        cron(daily_job, hour={7}, minute={0}, run_at_startup=True, unique=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_redis_url())
