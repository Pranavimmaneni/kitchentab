"""Run KitchenTab on this computer only, as it will run for real, to try it before deploying (`make house`).

A production build of the web app, the API, the notification worker, Postgres, Redis and MinIO, with
its own database (`kitchentab_house`, kept between runs). Open http://localhost:3100. Everything
listens on 127.0.0.1 only: nothing is reachable from the network. To try several roommates at once,
use separate browser profiles or private windows, one per person.

Sign-in codes land in Mailpit: http://localhost:8025. (Optional: real email through Gmail with
SMTP_USER / SMTP_PASSWORD / EMAIL_FROM in `.env.house`, gitignored.)

Plain http means no install-to-home-screen, no service worker and no Web Push; those come with the
HTTPS deployment (docs/LAUNCH.md).

    uv run --project apps/api python scripts/house.py [--skip-build] [--port 3100]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
WEB_DIR = ROOT / "apps" / "web"
STATE = ROOT / ".cache" / "house"
DB_NAME = "kitchentab_house"
BASE_DB = "postgresql+asyncpg://kitchentab:kitchentab@localhost:5432"
API_PORT = 8100
DIST = (
    "test-results/next-house"  # ignored by lint/tsc, like the other side-by-side builds
)


def exe(name: str) -> str:
    """Full path, so Windows finds `pnpm.cmd` and friends."""
    return shutil.which(name) or name


def secrets_once() -> dict[str, str]:
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / "state.json"
    if path.exists():
        data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        return data
    data = {
        k: secrets.token_urlsafe(32)
        for k in ("SESSION_SECRET", "TOKEN_HASH_PEPPER", "CRON_SECRET")
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.split(" #")[0].strip().strip('"')
    return out


def env_for(port: int) -> dict[str, str]:
    origin = f"http://localhost:{port}"
    mail = dotenv(ROOT / ".env.house")
    env = {
        **os.environ,
        **secrets_once(),
        "APP_ENV": "local",
        "PROFILE": "local",
        "DATABASE_URL": f"{BASE_DB}/{DB_NAME}",
        "REDIS_URL": "redis://localhost:6379/5",
        "WEB_ORIGIN": origin,
        "PUBLIC_LINK_BASE": origin,
        "STORAGE_PROVIDER": "s3",
        "S3_ENDPOINT": "http://localhost:9000",  # the browser uploads receipts straight to MinIO
        "S3_ACCESS_KEY": "minioadmin",
        "S3_SECRET_KEY": "minioadmin",
        "EXTRACTOR_PROVIDER": "local",
        "QUEUE_PROVIDER": "arq",
        "PUSH_PROVIDER": "fake",
        "SMS_PROVIDER": "none",
        "EMAIL_POSTAL_ADDRESS": mail.get("EMAIL_POSTAL_ADDRESS", "KitchenTab, at home"),
        "LOG_LEVEL": "INFO",
        # web
        "KT_API_ORIGIN": f"http://127.0.0.1:{API_PORT}",
        "KT_DIST_DIR": DIST,
        "NODE_ENV": "production",
    }
    if mail.get("SMTP_USER") and mail.get("SMTP_PASSWORD"):
        env.update(
            EMAIL_PROVIDER="smtp",
            SMTP_HOST=mail.get("SMTP_HOST", "smtp.gmail.com"),
            SMTP_PORT=mail.get("SMTP_PORT", "587"),
            SMTP_USER=mail["SMTP_USER"],
            SMTP_PASSWORD=mail["SMTP_PASSWORD"],
            EMAIL_FROM=mail.get("EMAIL_FROM", f"KitchenTab <{mail['SMTP_USER']}>"),
        )
    else:
        env.update(EMAIL_PROVIDER="fake", SMTP_URL="smtp://localhost:1025")
    return env


def run(cmd: list[str], cwd: Path, env: dict[str, str], what: str) -> None:
    print(f"== {what}", flush=True)
    if subprocess.run(cmd, cwd=cwd, env=env, check=False).returncode != 0:
        sys.exit(f"{what} failed")


def database(env: dict[str, str]) -> None:
    """Create the house database once (never dropped), then migrate."""
    probe = subprocess.run(
        [exe("docker"), "exec", "kitchentab-postgres-1", "psql", "-U", "kitchentab", "-d", "postgres", "-Atc",
         f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}'"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if probe.stdout.strip() != "1":
        run(
            [
                "docker",
                "exec",
                "kitchentab-postgres-1",
                "createdb",
                "-U",
                "kitchentab",
                DB_NAME,
            ],
            ROOT,
            env,
            "create database",
        )
    run([exe("uv"), "run", "alembic", "upgrade", "head"], API_DIR, env, "migrate")


def start(
    cmd: list[str], cwd: Path, env: dict[str, str], name: str
) -> subprocess.Popen[bytes]:
    log = (STATE / f"{name}.log").open("ab")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags
    )


def wait_for(url: str, what: str, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=5, follow_redirects=False).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    sys.exit(f"{what} did not come up; see {STATE}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", type=int, default=3100)
    parser.add_argument(
        "--skip-build", action="store_true", help="reuse the last web build"
    )
    args = parser.parse_args()
    env = env_for(args.port)
    run([exe("docker"), "compose", "-f", "infra/docker-compose.yml", "up", "-d", "postgres", "redis", "minio", "mailpit"],
        ROOT, env, "postgres, redis, minio, mailpit")  # fmt: skip
    database(env)
    if not args.skip_build:
        run(
            [exe("pnpm"), "-C", "apps/web", "build"],
            ROOT,
            env,
            "web build (a few minutes)",
        )
    uv = [exe("uv"), "run"]
    procs = {
        "api": start(
            [
                *uv,
                "uvicorn",
                "--factory",
                "app.main:create_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(API_PORT),
                "--timeout-keep-alive",
                "300",
            ],
            API_DIR,
            env,
            "api",
        ),
        "worker": start(
            [*uv, "arq", "app.workers.worker.WorkerSettings"], API_DIR, env, "worker"
        ),
    }
    wait_for(f"http://127.0.0.1:{API_PORT}/api/v1/health", "API")
    procs["web"] = start(
        [exe("pnpm"), "exec", "next", "start", "-H", "127.0.0.1", "-p", str(args.port)],
        WEB_DIR,
        env,
        "web",
    )
    wait_for(f"http://127.0.0.1:{args.port}/", "web")
    mail = (
        "Gmail"
        if env["EMAIL_PROVIDER"] == "smtp"
        else "Mailpit at http://localhost:8025"
    )
    print(
        f"\nKitchenTab is up: http://localhost:{args.port}  (sign-in codes: {mail})",
        flush=True,
    )
    print(
        f"Logs: {STATE}. Ctrl-C stops it; data stays in the {DB_NAME} database.",
        flush=True,
    )
    (STATE / "pids.json").write_text(
        json.dumps({k: p.pid for k, p in procs.items()}), encoding="utf-8"
    )
    try:
        while all(p.poll() is None for p in procs.values()):
            time.sleep(2)
        dead = [k for k, p in procs.items() if p.poll() is not None]
        print(f"stopped: {', '.join(dead)} exited; see {STATE}", flush=True)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs.values():
            if p.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    p.terminate()


if __name__ == "__main__":
    sys.exit(main())
