"""A real HTTP server for streaming tests (SSE) and restart tests (SYNC-21).

Runs uvicorn with the app's lifespan in a background thread on a free port. Cookies are not
`Secure` here (plain http on 127.0.0.1), so the settings point WEB_ORIGIN at the server itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import uvicorn

from app.main import create_app
from app.settings import Settings
from tests.harness.servers import free_port


class LiveServer:
    def __init__(self, settings: Settings, port: int | None = None, **state: Any) -> None:
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.settings = settings.model_copy(update={"web_origin": self.url, "public_link_base": self.url})
        self.state = state
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        app = create_app(self.settings)
        for key, value in self.state.items():
            setattr(app.state, key, value)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("live server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(10)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, timeout=30)


@asynccontextmanager
async def live_server(settings: Settings, **state: Any) -> AsyncIterator[LiveServer]:
    server = LiveServer(settings, **state)
    await asyncio.to_thread(server.start)
    try:
        yield server
    finally:
        await asyncio.to_thread(server.stop)


@dataclass
class SseEvent:
    event: str | None
    id: int | None
    data: Any


@dataclass
class SseStream:
    """Reads a `GET /houses/{id}/events` stream in the background and collects events."""

    client: httpx.AsyncClient
    house_id: str
    last_event_id: int | None = None
    events: list[SseEvent] = field(default_factory=list)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SseStream:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self.ready.wait(), 10)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):  # closing a stream on purpose
                await self._task

    async def _run(self) -> None:
        headers = {"Accept": "text/event-stream"}
        if self.last_event_id is not None:
            headers["Last-Event-ID"] = str(self.last_event_id)
        try:
            async with self.client.stream("GET", f"/api/v1/houses/{self.house_id}/events", headers=headers) as resp:
                assert resp.status_code == 200, await resp.aread()
                block: dict[str, str] = {}
                async for line in resp.aiter_lines():
                    if line == "":
                        if "data" in block:
                            ev = SseEvent(
                                block.get("event"),
                                int(block["id"]) if "id" in block else None,
                                json.loads(block["data"]),
                            )
                            self.events.append(ev)
                            if ev.event in ("ready", "resync"):
                                self.ready.set()
                        block = {}
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("retry:"):
                        continue
                    key, _, value = line.partition(":")
                    block[key] = value.lstrip(" ")
        finally:
            self.ready.set()
            self.closed.set()

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [e.data for e in self.events if e.event is None]

    @property
    def seqs(self) -> list[int]:
        return [int(m["seq"]) for m in self.messages]

    async def wait_for(self, count: int, timeout: float = 5.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while len(self.messages) < count:
            if time.monotonic() > deadline:
                raise TimeoutError(f"got {len(self.messages)} of {count} events: {self.seqs}")
            await asyncio.sleep(0.02)
        return self.messages

    async def wait_event(self, name: str, timeout: float = 5.0) -> SseEvent:
        deadline = time.monotonic() + timeout
        while True:
            for e in self.events:
                if e.event == name:
                    return e
            if time.monotonic() > deadline:
                raise TimeoutError(f"no {name} event; got {[e.event for e in self.events]}")
            await asyncio.sleep(0.02)
