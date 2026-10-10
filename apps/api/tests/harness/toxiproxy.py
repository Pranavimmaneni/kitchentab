"""Chaos proxy control (TEST_PLAN §2, CHAOS-*): a small client for Toxiproxy's HTTP API.

Toxiproxy runs from `infra/docker-compose.yml` (service `toxiproxy`, profile `chaos`; `make
test-chaos` starts it). Its API listens on :8474; proxies listen on 26400-26409 inside the
container, published on the same host ports, and reach services published on the host (the test
Postgres and Redis containers) through `host.docker.internal` (ADR-0024).

    tp = Toxiproxy.connect()
    pg = tp.proxy("kt_pg", listen_port=26400, upstream="host.docker.internal:5432")
    url = pg.url_for(database_url)          # the same DSN, through the proxy
    pg.disable(); pg.enable()               # "Postgres restarted": every connection drops
    pg.add_toxic("reset_peer", attributes={"timeout": 100})

Plain httpx (already a dependency); no client library.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
DEFAULT_URL = "http://127.0.0.1:8474"
PORT_RANGE = range(26400, 26410)  # published by the compose service
# Where a container reaches a port published on the host (Docker Desktop; `host-gateway` on Linux).
HOST_FROM_CONTAINER = os.environ.get("TOXIPROXY_UPSTREAM_HOST", "host.docker.internal")

Stream = Literal["upstream", "downstream"]


class ToxiproxyError(RuntimeError):
    pass


@dataclass
class Proxy:
    api: Toxiproxy
    name: str
    listen_port: int
    upstream: str

    @property
    def host_address(self) -> tuple[str, int]:
        """Where processes on the host connect to reach the upstream through the proxy."""
        return self.api.host, self.listen_port

    def url_for(self, dsn: str) -> str:
        """`dsn` with its host and port replaced by this proxy's (user, password, path kept)."""
        parts = urlsplit(dsn)
        userinfo = parts.netloc.rpartition("@")[0]
        netloc = f"{userinfo}@{self.api.host}:{self.listen_port}" if userinfo else f"{self.api.host}:{self.listen_port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def _update(self, **body: Any) -> None:
        self.api.request("POST", f"/proxies/{self.name}", json=body)

    def disable(self) -> None:
        """Closes every open connection and refuses new ones (the upstream "went away")."""
        self._update(enabled=False)

    def enable(self) -> None:
        self._update(enabled=True)

    def add_toxic(
        self,
        type: str,
        *,
        name: str | None = None,
        stream: Stream = "downstream",
        toxicity: float = 1.0,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        toxic = name or f"{type}_{stream}"
        self.api.request(
            "POST",
            f"/proxies/{self.name}/toxics",
            json={"name": toxic, "type": type, "stream": stream, "toxicity": toxicity, "attributes": attributes or {}},
        )
        return toxic

    def remove_toxic(self, name: str) -> None:
        self.api.request("DELETE", f"/proxies/{self.name}/toxics/{name}")

    def toxics(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self.api.request("GET", f"/proxies/{self.name}/toxics").json()
        return data

    def heal(self) -> None:
        """Enabled, no toxics."""
        for toxic in self.toxics():
            self.remove_toxic(str(toxic["name"]))
        self.enable()

    def delete(self) -> None:
        self.api.request("DELETE", f"/proxies/{self.name}", ok=(204, 404))


class Toxiproxy:
    def __init__(self, url: str = DEFAULT_URL) -> None:
        self.url = url.rstrip("/")
        self.host = urlsplit(self.url).hostname or "127.0.0.1"
        self._http = httpx.Client(base_url=self.url, timeout=10)

    @classmethod
    def connect(cls, url: str | None = None, *, start: bool = True, timeout: float = 60) -> Toxiproxy:
        """Connects to TOXIPROXY_URL (default :8474). If nothing answers and `start` is set, starts
        the compose service the way `make test-chaos` does, then waits for it."""
        tp = cls(url or os.environ.get("TOXIPROXY_URL", DEFAULT_URL))
        if tp.alive():
            return tp
        if start:
            cmd = [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "--profile",
                "chaos",
                "up",
                "-d",
                "--wait",
                "toxiproxy",
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)  # noqa: S603
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if tp.alive():
                return tp
            time.sleep(0.25)
        raise ToxiproxyError(f"Toxiproxy is not answering at {tp.url} (run `make test-chaos`)")

    def alive(self) -> bool:
        try:
            return self._http.get("/version").status_code == 200
        except httpx.HTTPError:
            return False

    def request(self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201, 204), **kw: Any) -> httpx.Response:
        resp = self._http.request(method, path, **kw)
        if resp.status_code not in ok:
            raise ToxiproxyError(f"{method} {path}: {resp.status_code} {resp.text}")
        return resp

    def proxy(self, name: str, *, listen_port: int, upstream: str) -> Proxy:
        """(Re)creates proxy `name`: listening on `listen_port` (one of PORT_RANGE), to `upstream`."""
        if listen_port not in PORT_RANGE:
            raise ValueError(f"listen_port must be one of the published ports {PORT_RANGE}")
        self.request("DELETE", f"/proxies/{name}", ok=(204, 404))
        self.request(
            "POST",
            "/proxies",
            json={"name": name, "listen": f"0.0.0.0:{listen_port}", "upstream": upstream, "enabled": True},
        )
        return Proxy(self, name, listen_port, upstream)

    def reset(self) -> None:
        """Every proxy enabled, every toxic removed."""
        self.request("POST", "/reset")

    def close(self) -> None:
        self._http.close()


def upstream_for(dsn: str) -> str:
    """`host:port` a container uses to reach the service `dsn` points at on the host."""
    parts = urlsplit(dsn)
    host = parts.hostname or "localhost"
    if host in ("localhost", "127.0.0.1", "::1"):
        host = HOST_FROM_CONTAINER
    return f"{host}:{parts.port or 5432}"
