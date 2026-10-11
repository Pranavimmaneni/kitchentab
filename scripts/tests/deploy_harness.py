"""Harness for the deploy-script tests (VERCEL-10, ADR-0022).

Each test gets a temp "repo" (KITCHENTAB_ROOT), a bin folder holding shims for the fake `vercel`,
`uv` and `make`, and a PATH made of only that folder plus the OS folder cmd.exe/sh need, so the
real Vercel CLI can never be resolved. The fakes record every call in calls.json.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts" / "deploy"
FAKES = Path(__file__).resolve().parent / "deploy_fakes"
IS_WINDOWS = os.name == "nt"
ORIGIN = "https://kitchentab.vercel.app"

sys.path.insert(0, str(DEPLOY))

# Variables whose values must never be printed or logged.
SECRET_NAMES = (
    "SESSION_SECRET",
    "TOKEN_HASH_PEPPER",
    "CRON_SECRET",
    "TEST_INBOX_SECRET",
    "VAPID_PRIVATE_KEY",
    "DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
    "PGPASSWORD",
    "BLOB_READ_WRITE_TOKEN",
    "SMTP_PASSWORD",
)


def _system_dirs() -> list[str]:
    if IS_WINDOWS:
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return [os.path.join(root, "System32")]
    return ["/usr/bin", "/bin"]


# The fakes are stdlib-only: run them on the base interpreter without site (-S -I), which starts
# about twice as fast as the venv launcher on Windows.
FAKE_PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def make_bin(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = f'"{FAKE_PYTHON}" -S -I'

    for tool, script, extra in (
        ("vercel", "fake_vercel.py", ""),
        ("uv", "fake_tool.py", "uv"),
        ("make", "fake_tool.py", "make"),
    ):
        target = FAKES / script
        if IS_WINDOWS:
            (bin_dir / f"{tool}.cmd").write_text(
                f'@{py} "{target}" {extra} %*\r\n@exit /b %ERRORLEVEL%\r\n',
                encoding="utf-8",
            )
        else:
            shim = bin_dir / tool
            shim.write_text(
                f'#!/bin/sh\nexec {py} "{target}" {extra} "$@"\n', encoding="utf-8"
            )
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


DECOY_TEXT = "decoy: the Python vercel package's console script"


def make_venv_decoy(venv: Path) -> Path:
    """A virtualenv-shaped folder (pyvenv.cfg + Scripts/bin) holding a `vercel` that is not the CLI,
    like the one the Vercel Queues SDK dependency installs in apps/api/.venv. Returns its bin folder."""
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = /nowhere\n", encoding="utf-8")
    bin_dir = venv / ("Scripts" if IS_WINDOWS else "bin")
    bin_dir.mkdir(exist_ok=True)
    if IS_WINDOWS:
        (bin_dir / "vercel.cmd").write_text(
            f"@echo {DECOY_TEXT} 1>&2\r\n@exit /b 9\r\n", encoding="utf-8"
        )
    else:
        shim = bin_dir / "vercel"
        shim.write_text(
            f'#!/bin/sh\necho "{DECOY_TEXT}" >&2\nexit 9\n', encoding="utf-8"
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


@dataclass
class Run:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + "\n" + self.err


class Harness:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.root = tmp / "repo"
        (self.root / "apps" / "api").mkdir(parents=True)
        (self.root / "vercel.json").write_text("{}", encoding="utf-8")
        self.bin = tmp / "bin"
        make_bin(self.bin)
        self.state_dir = tmp / "fake-vercel"
        self.state_dir.mkdir()

    # ------------------------------------------------------------------ running scripts

    def env(
        self, fail: str | None = None, extra: dict[str, str] | None = None
    ) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.upper().startswith(
                ("VERCEL", "NEON", "FAKE_", "KITCHENTAB", "DATABASE_")
            )
        }
        env["PATH"] = os.pathsep.join([str(self.bin), *_system_dirs()])
        env["KITCHENTAB_ROOT"] = str(self.root)
        env["FAKE_VERCEL_STATE"] = str(self.state_dir)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if fail:
            env["FAKE_VERCEL_FAIL"] = fail
        env.update(extra or {})
        return env

    def run(
        self,
        script: str,
        *args: str,
        stdin: str = "",
        fail: str | None = None,
        extra_env: dict[str, str] | None = None,
        timeout: float = 180,
    ) -> Run:
        proc = subprocess.run(
            [sys.executable, str(DEPLOY / script), *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env(fail, extra_env),
            cwd=self.root,
            timeout=timeout,
            check=False,
        )
        return Run(proc.returncode, proc.stdout, proc.stderr)

    def stub_path(self) -> str | None:
        return shutil.which("vercel", path=self.env()["PATH"])

    # ------------------------------------------------------------------ fake state

    def calls(self, tool: str | None = None) -> list[dict]:
        path = self.state_dir / "calls.json"
        calls = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [c for c in calls if tool is None or c["tool"] == tool]

    def vercel_argvs(self) -> list[list[str]]:
        return [c["argv"] for c in self.calls("vercel")]

    def reset_calls(self) -> None:
        (self.state_dir / "calls.json").unlink(missing_ok=True)

    def state(self) -> dict:
        return json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> None:
        (self.state_dir / "state.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )

    def env_vars(self, environment: str = "production") -> dict[str, str]:
        return dict(self.state()["env"][environment])

    def secret_values(self) -> list[str]:
        if not (self.state_dir / "state.json").exists():
            return []
        values = []
        for env in self.state()["env"].values():
            for name in SECRET_NAMES:
                if env.get(name):
                    values.append(env[name])
                    if "://" in env[name]:
                        from urllib.parse import urlsplit

                        password = urlsplit(env[name]).password
                        if password:
                            values.append(password)
        return values

    def assert_no_secrets(self, *runs: Run, extra: tuple[str, ...] = ()) -> None:
        logged = (
            (self.state_dir / "calls.json").read_text(encoding="utf-8")
            if self.calls()
            else ""
        )
        haystacks = [r.text for r in runs] + [logged]
        for value in [v for v in (*self.secret_values(), *extra) if len(v) >= 8]:
            for text in haystacks:
                assert value not in text, f"secret ({len(value)} chars) leaked"

    def provision(
        self,
        origin: str = ORIGIN,
        *,
        deployments: int = 1,
        email_provider: str = "testinbox",
    ) -> None:
        """State as if `make deploy-setup` and `make deploy` had already run."""
        password = "npg_" + secrets.token_hex(8)
        host = "ep-fancy-snow-a5k2j3l4"

        def env_for() -> dict[str, str]:
            env = {
                "APP_ENV": "prod",
                "PROFILE": "vercel",
                "QUEUE_PROVIDER": "vercel",
                "STORAGE_PROVIDER": "vercel_blob",
                "PUSH_PROVIDER": "webpush",
                "SMS_PROVIDER": "none",
                "EXTRACTOR_PROVIDER": "local",
                "EMAIL_PROVIDER": email_provider,
                "SESSION_SECRET": secrets.token_urlsafe(64),
                "TOKEN_HASH_PEPPER": secrets.token_urlsafe(48),
                "CRON_SECRET": secrets.token_urlsafe(48),
                "TEST_INBOX_SECRET": secrets.token_urlsafe(64),
                "VAPID_PUBLIC_KEY": "B" + secrets.token_urlsafe(63),
                "VAPID_PRIVATE_KEY": secrets.token_urlsafe(32),
                "DATABASE_URL": f"postgresql://neondb_owner:{password}@{host}-pooler.us-east-2.aws.neon.tech/neondb"
                "?sslmode=require&channel_binding=require",
                "DATABASE_URL_UNPOOLED": f"postgresql://neondb_owner:{password}@{host}.us-east-2.aws.neon.tech/neondb"
                "?sslmode=require&channel_binding=require",
                "BLOB_READ_WRITE_TOKEN": "vercel_blob_rw_" + secrets.token_urlsafe(24),
                "WEB_ORIGIN": origin,
                "PUBLIC_LINK_BASE": origin,
                "GOOGLE_REDIRECT_URI": origin + "/api/v1/auth/google/callback",
                "VAPID_SUBJECT": origin,
            }
            return env

        state = {
            "logged_in": True,
            "user": "pranav",
            "project": "kitchentab",
            "env": {"production": env_for(), "preview": env_for(), "development": {}},
            "integrations": ["neon"],
            "blob_stores": ["kitchentab-receipts"],
            "deployments": [],
            "aliases": [origin, "https://kitchentab-pranavs-projects.vercel.app"],
            "current": None,
            "calls": 0,
        }
        for i in range(deployments):
            url = f"https://kitchentab-old{i:05d}x-pranavs-projects.vercel.app"
            state["deployments"].append(
                {
                    "url": url,
                    "target": "production",
                    "status": "Ready",
                    "env_names": [],
                    "web_origin": origin,
                }
            )
            state["current"] = url
        self.save_state(state)
        vercel_dir = self.root / ".vercel"
        vercel_dir.mkdir(exist_ok=True)
        (vercel_dir / "project.json").write_text(
            json.dumps(
                {
                    "projectId": "prj_fake123",
                    "orgId": "team_fake456",
                    "projectName": "kitchentab",
                }
            ),
            encoding="utf-8",
        )
        (vercel_dir / "kitchentab-deploy.json").write_text(
            json.dumps({"production_url": origin}), encoding="utf-8"
        )
