"""Race helper (TEST_PLAN §2): start n calls behind a barrier so they hit Postgres together.

    results = await run_concurrently(20, lambda i: client.put(...))

`sync_iterations()` is how many times a SYNC test repeats: 200 under `make test-sync`
(SYNC_ITERATIONS=200), a smoke-level 3 under `make test`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome[T]:
    index: int
    value: T | None
    error: BaseException | None

    @property
    def ok(self) -> bool:
        return self.error is None


async def run_concurrently[T](n: int, fn: Callable[[int], Awaitable[T]], *, timeout: float = 30.0) -> list[Outcome[T]]:
    barrier = asyncio.Barrier(n)

    async def one(i: int) -> Outcome[T]:
        await barrier.wait()
        try:
            return Outcome(i, await fn(i), None)
        except Exception as exc:  # collected, not raised: tests assert on outcomes
            return Outcome(i, None, exc)

    async with asyncio.timeout(timeout):
        return list(await asyncio.gather(*(one(i) for i in range(n))))


def sync_iterations(default: int = 3) -> int:
    return int(os.environ.get("SYNC_ITERATIONS", default))
