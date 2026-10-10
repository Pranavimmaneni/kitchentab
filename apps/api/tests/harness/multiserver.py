"""The API as production runs it: several worker processes behind one port (uvicorn --workers).

In-process tests share one event loop and one GIL between every client and the server, so they
can't say anything about latency under concurrency. SYNC-24 and other timing checks run their
clients against this instead. Settings come from the test environment (conftest), with the
origins pointed at the server itself (plain http on 127.0.0.1, so cookies are not `Secure`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from tests.harness.api import Api
from tests.harness.servers import free_port

API_DIR = Path(__file__).resolve().parents[2]


class MultiServer:
    """Several API instances on their own ports, like serverless instances: each client is pinned to
    one of them (round robin), so a write and the stream that must carry it usually differ."""

    def __init__(self, urls: list[str]) -> None:
        self.urls = urls
        self.url = urls[0]
        self.logs: list[Path] = []
        self._clients: list[httpx.AsyncClient] = []

    @property
    def log(self) -> str:
        return ", ".join(str(p) for p in self.logs)

    def api(self) -> Api:
        client = httpx.AsyncClient(base_url=self.urls[len(self._clients) % len(self.urls)], timeout=60)
        self._clients.append(client)
        return Api(client)

    async def aclose(self) -> None:
        for c in self._clients:
            await c.aclose()


LOG_DIR = API_DIR.parents[1] / ".cache" / "multiserver"


def _spawn(cmd: list[str], env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    log.parent.mkdir(parents=True, exist_ok=True)
    out = log.open("wb")  # the workers' stdout/stderr, kept for diagnosis (path printed on failure)
    return spawn_tree(cmd, cwd=API_DIR, env=env, stdout=out)


def spawn_tree(cmd: list[str], *, cwd: Path, env: dict[str, str], stdout: Any) -> subprocess.Popen[bytes]:
    """Start a process that will spawn workers, in its own process group so `stop_tree` gets them all."""
    kwargs: dict[str, Any] = {"cwd": cwd, "env": env, "stdout": stdout, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)  # noqa: S603


def stop_tree(proc: subprocess.Popen[bytes], timeout: float = 20) -> None:
    """Stop uvicorn and every worker it spawned. Terminating only the supervisor leaves the workers
    running on Windows, each holding its database pool (they once exhausted Postgres' 100 slots)."""
    if proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # noqa: S607
                capture_output=True,
                check=False,
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        proc.kill()
        proc.wait(5)


STARTED = b"Application startup complete"


def wait_workers_ready(
    url: str, proc: subprocess.Popen[bytes], log: Path, workers: int, *, offset: int = 0, timeout: float = 120
) -> None:
    """Ready means every worker finished startup, not just the first: on a busy machine the others
    can take half a minute more, and a request handed to a starting worker waits for it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"API workers exited with {proc.returncode}; see {log}")
        with contextlib.suppress(OSError):
            with log.open("rb") as f:
                f.seek(offset)
                started = f.read().count(STARTED)
            if started >= max(1, workers):
                with contextlib.suppress(httpx.HTTPError):
                    if httpx.get(f"{url}/api/v1/health", timeout=5).status_code == 200:
                        return
        time.sleep(0.2)
    raise RuntimeError(f"API workers did not become ready; see {log}")


def server_cmd(port: int, host: str = "127.0.0.1") -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "info",  # the startup line tells when it is up; requests are not logged
        "--no-access-log",
        # Clients sit idle while 20 people sign in; uvicorn's 5 s default would close their pooled
        # connections and the next request would race the close (ReadError / hang).
        "--timeout-keep-alive",
        "300",
    ]


@asynccontextmanager
async def multi_process_server(workers: int = 4) -> AsyncIterator[MultiServer]:
    """`workers` separate single-process instances. Not `uvicorn --workers`: on Windows its shared
    listening socket leaves some new connections unanswered for 40 s and more (ADR-0029)."""
    ports = [free_port() for _ in range(workers)]
    urls = [f"http://127.0.0.1:{port}" for port in ports]
    procs: list[subprocess.Popen[bytes]] = []
    server = MultiServer(urls)
    try:
        for port, url in zip(ports, urls, strict=True):
            env = {
                **os.environ,
                "WEB_ORIGIN": url,
                "PUBLIC_LINK_BASE": url,
                "GOOGLE_REDIRECT_URI": f"{url}/api/v1/auth/google/callback",
            }
            log = LOG_DIR / f"api-{port}.log"
            server.logs.append(log)
            procs.append(await asyncio.to_thread(_spawn, server_cmd(port), env, log))
        await asyncio.gather(
            *(
                asyncio.to_thread(wait_workers_ready, url, proc, log, 1)
                for url, proc, log in zip(urls, procs, server.logs, strict=True)
            )
        )
        yield server
    finally:
        await server.aclose()
        await asyncio.gather(*(asyncio.to_thread(stop_tree, proc) for proc in procs))
