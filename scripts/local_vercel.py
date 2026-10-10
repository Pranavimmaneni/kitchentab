"""`make local-vercel-up` / `make local-vercel-down`: the TARGET=local-vercel rehearsal (ADR-0028).

The production builds of both Vercel services, run the way Vercel runs them, behind an edge that
does what Vercel's edge does for this project:

    https://localhost:8443  lv-edge (Caddy, compose): vercel.json routing, HTTPS from Caddy's
                            internal CA, 4.5 MB request bodies -> 413, 300 s max duration
      /api/blob-upload, /api/blob-view, /(.*)  ->  next start (production build)    :3030
      /api/(.*)                                ->  uvicorn app.vercel_entry:app       :8030
    https://localhost:8444  the Blob store's own origin: the fake Blob service (compose)
    vercel.queue.devserver  :8031, and the `outbox-drain` push subscriber driven by the SDK's
                            poll_and_handle (scripts/local_vercel_drain.py)
    PgBouncer (transaction pooling) :6432 = the pooled DATABASE_URL; lv-postgres :5442 = the
                            direct URL for migrations; database kitchentab_lv in its own container
    fake push service       :4020 (launch-check's test subscription)

`vercel build` / `vercel dev` are not used: both need a linked project (`vercel pull`), which the
rehearsal must never create (BUILD_ALL §3 rule 8). Secrets are generated once and kept, with the
run state, in .cache/local-vercel/state.json (gitignored; never printed).

    uv run --project apps/api python scripts/local_vercel.py up [--skip-build] [--fresh-secrets]
    uv run --project apps/api python scripts/local_vercel.py down [--drop-db]
    uv run --project apps/api python scripts/local_vercel.py status

`LOCAL_VERCEL_HOST` (default localhost) puts another hostname or LAN IP in the certificate and the
origin, so a phone on the same network can open the rehearsal (docs/decisions/0028).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
WEB_DIR = ROOT / "apps" / "web"
STATE_DIR = ROOT / ".cache" / "local-vercel"
STATE_FILE = STATE_DIR / "state.json"
LOG_DIR = STATE_DIR / "logs"
CADDY_DATA = STATE_DIR / "caddy"
CA_FILE = CADDY_DATA / "caddy" / "pki" / "authorities" / "local" / "root.crt"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]
PROFILE = ["--profile", "local-vercel"]
CONTAINERS = ["lv-postgres", "lv-pgbouncer", "lv-fake-blob", "lv-fake-push", "lv-edge"]
DIST_DIR = (
    "test-results/next-lv"  # ignored by eslint and tsc (next.config.ts KT_DIST_DIR)
)
# next.config.ts names the service worker after a custom distDir (/sw-<dir>.js) so side-by-side
# builds never share a precache list; production (default distDir) serves /sw.js.
SW_PATH = "/sw-next-lv.js"
DB_NAME = "kitchentab_lv"
IS_WINDOWS = os.name == "nt"

# Ports: 3000/8000 (demo), 3010/8010 and 3020/8020 (Playwright suites) are taken (ADR-0028).
PORTS = {
    "edge": int(os.environ.get("LV_EDGE_PORT", "8443")),
    "blob": int(os.environ.get("LV_BLOB_PORT", "8444")),
    "web": int(os.environ.get("LV_WEB_PORT", "3030")),
    "api": int(os.environ.get("LV_API_PORT", "8030")),
    "queue": int(os.environ.get("LV_QUEUE_PORT", "8031")),
    "pgbouncer": 6432,
    "postgres": 5442,  # lv-postgres (its own container)
    "push": int(os.environ.get("LV_PUSH_PORT", "4020")),
}


def say(msg: str) -> None:
    print(msg, flush=True)


class UpError(Exception):
    pass


# ---------------------------------------------------------------------------- state and secrets


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def new_secrets() -> dict[str, str]:
    """Real-looking values, generated like `make deploy-setup` does (deploylib, ADR-0022)."""
    sys.path.insert(0, str(ROOT / "scripts" / "deploy"))
    from deploylib import new_vapid_keys

    public, private = new_vapid_keys()
    store = secrets.token_hex(8)
    return {
        "SESSION_SECRET": secrets.token_urlsafe(64),
        "TOKEN_HASH_PEPPER": secrets.token_urlsafe(48),
        "CRON_SECRET": secrets.token_urlsafe(48),
        "TEST_INBOX_SECRET": secrets.token_urlsafe(64),
        "VAPID_PUBLIC_KEY": public,
        "VAPID_PRIVATE_KEY": private.reveal(),
        "BLOB_READ_WRITE_TOKEN": f"vercel_blob_rw_{store}_{secrets.token_hex(24)}",
    }


def host() -> str:
    return os.environ.get("LOCAL_VERCEL_HOST", "localhost")


def origin() -> str:
    return f"https://{host()}:{PORTS['edge']}"


def blob_origin() -> str:
    return f"https://{host()}:{PORTS['blob']}"


def api_env(sec: dict[str, str]) -> dict[str, str]:
    """The API's environment: what `make deploy-setup` sets in production, with the local stand-ins
    for Neon, Blob, Queues and the push service. Every provider is explicit so the developer's
    .env (read by Settings) can't change the rehearsal."""
    pooled = (
        f"postgresql://kitchentab:kitchentab@127.0.0.1:{PORTS['pgbouncer']}/{DB_NAME}"
    )
    # Pooled: a libpq URL as Neon hands it out (settings.asyncpg_url converts it). Direct: the
    # SQLAlchemy form `make deploy` passes to Alembic (migrations/env.py reads it as is).
    direct = f"postgresql+asyncpg://kitchentab:kitchentab@127.0.0.1:{PORTS['postgres']}/{DB_NAME}"
    return {
        # Deployed mode can't run locally: it requires STORAGE_PROVIDER=vercel_blob, non-localhost
        # origins and real push-service endpoints. `preflight()` proves this exact set would boot
        # with APP_ENV=staging; ADR-0028 lists what else differs.
        "APP_ENV": "test",
        "TEST_ROUTES_ENABLED": "false",
        "PROFILE": "vercel",
        "WEB_ORIGIN": origin(),
        "PUBLIC_LINK_BASE": origin(),
        "SESSION_SECRET": sec["SESSION_SECRET"],
        "TOKEN_HASH_PEPPER": sec["TOKEN_HASH_PEPPER"],
        "CRON_SECRET": sec["CRON_SECRET"],
        "DATABASE_URL": pooled,
        "DATABASE_DIRECT_URL": direct,
        "STORAGE_PROVIDER": "fakeblob",
        "FAKE_BLOB_URL": blob_origin(),
        "BLOB_READ_WRITE_TOKEN": sec["BLOB_READ_WRITE_TOKEN"],
        "QUEUE_PROVIDER": "vercel",
        "VERCEL_QUEUE_BASE_URL": f"http://127.0.0.1:{PORTS['queue']}",
        "VERCEL_QUEUE_TOKEN": "vc-dev-token",  # what `vercel dev` hands its services
        "VERCEL_REGION": "iad1",
        "EMAIL_PROVIDER": "testinbox",
        "TEST_INBOX_SECRET": sec["TEST_INBOX_SECRET"],
        "EMAIL_FROM": "KitchenTab <hello@example.com>",
        "EMAIL_POSTAL_ADDRESS": "KitchenTab rehearsal, 1 Test Street, Testville",
        "SMS_PROVIDER": "none",
        "EXTRACTOR_PROVIDER": "local",
        "PUSH_PROVIDER": "webpush",
        "VAPID_PUBLIC_KEY": sec["VAPID_PUBLIC_KEY"],
        "VAPID_PRIVATE_KEY": sec["VAPID_PRIVATE_KEY"],
        "VAPID_SUBJECT": "mailto:hello@example.com",
        "RATE_LIMIT_WRITES_PER_MIN": "60",
        "RATE_LIMIT_READS_PER_MIN": "300",
        "LOG_LEVEL": "INFO",
        # The API reads the fake Blob through the edge's HTTPS origin, so it trusts the local CA.
        "SSL_CERT_FILE": str(CA_FILE),
        "PYTHONUNBUFFERED": "1",
    }


def compose_env(sec: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "LOCAL_VERCEL_HOST": host(),
            "LV_EDGE_PORT": str(PORTS["edge"]),
            "LV_BLOB_PORT": str(PORTS["blob"]),
            "LV_WEB_PORT": str(PORTS["web"]),
            "LV_API_PORT": str(PORTS["api"]),
            "LV_PUSH_PORT": str(PORTS["push"]),
            "LV_BLOB_RW_TOKEN": sec["BLOB_READ_WRITE_TOKEN"],
        }
    )
    return env


# ---------------------------------------------------------------------------- processes


def run(
    cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, what: str
) -> None:
    say(f"$ {' '.join(cmd[:6])}{' ...' if len(cmd) > 6 else ''}")
    proc = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if proc.returncode != 0:
        raise UpError(f"{what} failed (exit {proc.returncode})")


PROCS: dict[
    int, subprocess.Popen[bytes]
] = {}  # started by this run: exit codes for errors


def spawn(name: str, cmd: list[str], *, cwd: Path, env: dict[str, str]) -> int:
    """Start a long-running process detached from this shell; output goes to logs/<name>.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = (LOG_DIR / f"{name}.log").open("ab")
    kwargs: dict[str, Any] = {}
    if IS_WINDOWS:
        # Out of the caller's job object too: `uv run` (and some shells) put children in a job
        # that is closed, killing them, when the caller exits.
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        kwargs["start_new_session"] = True

    def popen(**kw: Any) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            **kw,
        )

    try:
        proc = popen(**kwargs)
    except OSError:  # the job doesn't allow breakaway: best effort
        kwargs["creationflags"] &= ~subprocess.CREATE_BREAKAWAY_FROM_JOB
        proc = popen(**kwargs)
    say(f"  started {name} (pid {proc.pid}); log: {LOG_DIR / (name + '.log')}")
    PROCS[proc.pid] = proc
    return proc.pid


def alive(pid: int) -> bool:
    if IS_WINDOWS:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_tree(pid: int) -> None:
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        pass


def port_open(port: int, host_: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host_, port)) == 0


def wait_for(
    what: str, check: Any, timeout: float, pids: list[int] | None = None
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                say(f"  ready: {what}")
                return
        except Exception:  # noqa: BLE001,S110 - still starting
            pass
        for pid in pids or []:
            proc = PROCS.get(pid)
            code = proc.poll() if proc is not None else (None if alive(pid) else "?")
            if code is not None:
                raise UpError(
                    f"{what}: the process exited (code {code}); see {LOG_DIR}"
                )
        time.sleep(0.5)
    raise UpError(f"{what} not ready after {timeout:.0f} s; see {LOG_DIR}")


def https_get(url: str) -> int:
    import httpx

    ctx = ssl.create_default_context(cafile=str(CA_FILE))
    return httpx.get(url, verify=ctx, timeout=5, follow_redirects=False).status_code


# ---------------------------------------------------------------------------- steps


def preflight(env: dict[str, str]) -> None:
    """Deployed-mode validation (settings.py) of this exact configuration, with only the three
    things that can't be local swapped for production-shaped values."""
    staged = dict(os.environ)
    staged.update(env)
    staged.update(
        {
            "APP_ENV": "staging",
            "STORAGE_PROVIDER": "vercel_blob",
            "WEB_ORIGIN": "https://kitchentab-rehearsal.vercel.app",
            "PUBLIC_LINK_BASE": "https://kitchentab-rehearsal.vercel.app",
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", "from app.settings import Settings; Settings()"],
        cwd=API_DIR,
        env=staged,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        tail = [ln for ln in proc.stderr.splitlines() if ln.strip().startswith("- ")]
        raise UpError(
            "deployed-mode settings check failed:\n"
            + "\n".join(tail or proc.stderr.splitlines()[-5:])
        )
    say(
        "  deployed-mode settings check (APP_ENV=staging, vercel_blob, https origin): OK"
    )


def build_web() -> None:
    """`next build` exactly as Vercel runs it for the web service (no KT_API_ORIGIN proxy).
    Next rewrites tsconfig.json's include list for a custom distDir; it's restored after."""
    node = shutil.which("node")
    if node is None:
        raise UpError("node is not on PATH")
    tsconfig = WEB_DIR / "tsconfig.json"
    before = tsconfig.read_bytes()
    env = web_env()
    try:
        run(
            [
                node,
                str(WEB_DIR / "node_modules" / "next" / "dist" / "bin" / "next"),
                "build",
            ],
            cwd=WEB_DIR,
            env=env,
            what="next build",
        )
    finally:
        if tsconfig.read_bytes() != before:
            tsconfig.write_bytes(before)


def web_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("KT_API_ORIGIN", "KT_DEV_COMPONENTS")
    }
    env.update(
        {
            "KT_DIST_DIR": DIST_DIR,
            "NEXT_TELEMETRY_DISABLED": "1",
            "NODE_ENV": "production",
            # Server components fetch the API through the public origin (lib/api/server.ts), i.e.
            # through the edge's HTTPS: Node trusts the local CA as it trusts Vercel's cert in prod.
            "NODE_EXTRA_CA_CERTS": str(CA_FILE),
        }
    )
    return env


# What each recorded process runs: a PID is only killed if it still runs this (PIDs get reused).
MARKERS = {
    "queue": "vercel.queue.devserver",
    "api": "app.vercel_entry:app",
    "drain": "local_vercel_drain.py",
    "web": "next",
}


def command_line(pid: int) -> str:
    if IS_WINDOWS:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip()
    try:
        return " ".join(
            Path(f"/proc/{pid}/cmdline").read_bytes().decode().split(chr(0))
        )
    except OSError:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip()


def stop_processes(state: dict[str, Any]) -> None:
    for name, pid in (state.get("pids") or {}).items():
        if not pid or not alive(int(pid)):
            continue
        marker = MARKERS.get(name, "")
        if marker and marker not in command_line(int(pid)):
            say(f"  pid {pid} ({name}) now belongs to another program; left alone")
            continue
        kill_tree(int(pid))
        say(f"  stopped {name} (pid {pid})")
    state["pids"] = {}


def up(args: argparse.Namespace) -> int:
    state = load_state()
    if args.fresh_secrets or "secrets" not in state:
        state["secrets"] = new_secrets()
    sec: dict[str, str] = state["secrets"]
    stop_processes(state)
    save_state(state)

    for name in ("web", "api", "queue"):
        deadline = (
            time.monotonic() + 15
        )  # a process just stopped may hold its port briefly
        while port_open(PORTS[name]) and time.monotonic() < deadline:
            time.sleep(0.5)
        if port_open(PORTS[name]):
            raise UpError(
                f"port {PORTS[name]} ({name}) is in use; set LV_{name.upper()}_PORT to another port"
            )

    say("== containers: PgBouncer, fake Blob, fake push, edge (Caddy)")
    CADDY_DATA.mkdir(parents=True, exist_ok=True)
    run(
        [*COMPOSE, *PROFILE, "up", "-d", "--build", "--wait", *CONTAINERS],
        env=compose_env(sec),
        what="docker compose up",
    )
    wait_for("local CA root certificate", CA_FILE.exists, 30)

    env = {**os.environ, **api_env(sec)}
    say("== migrations (direct URL, never through PgBouncer)")
    run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        what="alembic upgrade head",
    )
    preflight(api_env(sec))

    if args.skip_build and (WEB_DIR / DIST_DIR / "BUILD_ID").exists():
        say("== next build skipped (--skip-build)")
    else:
        say("== next build (production)")
        build_web()

    say("== services")
    pids: dict[str, int] = {}
    pids["queue"] = spawn(
        "queue",
        [
            sys.executable,
            "-m",
            "vercel.queue.devserver",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORTS["queue"]),
        ],
        cwd=API_DIR,
        env=env,
    )
    pids["api"] = spawn(
        "api",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.vercel_entry:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORTS["api"]),
            "--no-access-log",
            "--timeout-graceful-shutdown",
            "5",
        ],
        cwd=API_DIR,
        env=env,
    )
    pids["drain"] = spawn(
        "drain",
        [sys.executable, str(ROOT / "scripts" / "local_vercel_drain.py")],
        cwd=API_DIR,
        env={**env, "PYTHONPATH": str(API_DIR)},
    )
    node = shutil.which("node") or "node"
    pids["web"] = spawn(
        "web",
        [
            node,
            str(WEB_DIR / "node_modules" / "next" / "dist" / "bin" / "next"),
            "start",
            "-H",
            "127.0.0.1",
            "-p",
            str(PORTS["web"]),
        ],
        cwd=WEB_DIR,
        env=web_env(),
    )
    state["pids"] = pids
    state.update(
        {
            "base_url": origin(),
            "blob_url": blob_origin(),
            "push_url": f"http://localhost:{PORTS['push']}",
            "ca_file": str(CA_FILE),
            "sw_path": SW_PATH,
            "ports": PORTS,
            "database": DB_NAME,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    save_state(state)

    wait_for("queue devserver", lambda: port_open(PORTS["queue"]), 30, [pids["queue"]])
    wait_for(
        "API through the edge",
        lambda: https_get(origin() + "/api/v1/health") == 200,
        90,
        [pids["api"]],
    )
    wait_for(
        "web through the edge",
        lambda: https_get(origin() + "/manifest.webmanifest") == 200,
        120,
        [pids["web"]],
    )
    wait_for("Blob origin", lambda: https_get(blob_origin() + "/health") == 200, 30)
    time.sleep(2)
    if not alive(pids["drain"]):
        raise UpError(f"the queue subscriber exited; see {LOG_DIR / 'drain.log'}")

    say("")
    say(f"local-vercel is up: {origin()}")
    say(
        f"  Blob origin {blob_origin()} | fake push http://localhost:{PORTS['push']} | database {DB_NAME} via PgBouncer :6432"
    )
    say(f"  CA certificate (trust it in a browser or phone): {CA_FILE}")
    say(f"  logs: {LOG_DIR}    stop: make local-vercel-down")
    return 0


def down(args: argparse.Namespace) -> int:
    state = load_state()
    stop_processes(state)
    save_state(state)
    run([*COMPOSE, *PROFILE, "rm", "-s", "-f", *CONTAINERS], what="docker compose rm")
    if args.drop_db:  # the rehearsal database lives in its own volume
        run(
            ["docker", "volume", "rm", "-f", "kitchentab_lvpgdata"],
            what="remove the kitchentab_lv volume",
        )
    say("local-vercel is down.")
    return 0


def status(_: argparse.Namespace) -> int:
    state = load_state()
    if not state.get("pids"):
        say("local-vercel: not running")
        return 1
    ok = True
    for name, pid in state["pids"].items():
        up_ = alive(int(pid))
        ok = ok and up_
        say(f"  {name:6} pid {pid}: {'running' if up_ else 'stopped'}")
    say(f"  base URL {state.get('base_url')}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("up")
    p_up.add_argument(
        "--skip-build", action="store_true", help="reuse the last next build"
    )
    p_up.add_argument(
        "--fresh-secrets", action="store_true", help="generate new secrets"
    )
    p_down = sub.add_parser("down")
    p_down.add_argument("--drop-db", action="store_true", help=f"also drop {DB_NAME}")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        return {"up": up, "down": down, "status": status}[args.cmd](args)
    except UpError as err:
        print(f"ERROR: {err}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
