"""The `outbox-drain` push subscriber for the TARGET=local-vercel rehearsal (ADR-0013, ADR-0028).

On Vercel, the queue service calls the function compiled from `app/queues/outbox.py` (declared
under `[[tool.vercel.subscribers]]`) for every message. `python -m vercel.queue.devserver` is a
broker only: it stores, leases and acknowledges messages but has no way to call a function. So
this process drives the *same* subscriber object with the SDK's `poll_and_handle`, against the
devserver at VERCEL_QUEUE_BASE_URL: receive, lease, call `drain_outbox`, acknowledge on success,
redeliver after `retry_after` on failure, as the hosted service does.

    cd apps/api && PYTHONPATH=. python ../../scripts/local_vercel_drain.py   (scripts/local_vercel.py)
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.queues.outbox import drain_outbox
from vercel.queue import poll_and_handle

log = logging.getLogger("local_vercel.drain")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    delay = 0.5
    while True:
        try:
            log.info("polling topic outbox-drain")
            await poll_and_handle(drain_outbox, interval=0.25)
        except Exception as exc:  # noqa: BLE001 - the devserver may still be starting
            log.warning(
                "queue poll failed (%s); retrying in %.1f s", type(exc).__name__, delay
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
