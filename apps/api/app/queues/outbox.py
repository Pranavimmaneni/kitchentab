"""Push subscriber for topic `outbox-drain` (ADR-0013). Vercel compiles this module into a
queue-triggered function; locally `vercel.queue.devserver` or the embedded queue drives it."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from vercel.queue import subscribe

from app.adapters.queue import TOPIC, kicker
from app.clock import ControllableClock
from app.db import make_engine, make_sessionmaker
from app.services.notify import NotifyDeps, drain
from app.services.notify_runtime import build_deps
from app.settings import get_settings


@lru_cache(maxsize=1)
def _deps() -> NotifyDeps:
    settings = get_settings()
    # No pool here (ADR-0032): a queue invocation may run in a fresh event loop.
    engine = make_engine(settings.database_url, serverless=settings.profile == "vercel")
    return build_deps(settings, make_sessionmaker(engine), ControllableClock(), kicker(settings))


@subscribe(topic=TOPIC, retry_after=60, max_attempts=10)
async def drain_outbox(message: dict[str, Any]) -> None:
    """Every message means "drain now"; the drain itself finds what is due (idempotent)."""
    await drain(_deps())
