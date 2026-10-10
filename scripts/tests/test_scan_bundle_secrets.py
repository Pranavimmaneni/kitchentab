"""Self-test of scripts/scan_bundle_secrets.py (SEC-07, client bundle half) on a fake bundle."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "scan_bundle_secrets", ROOT / "scripts" / "scan_bundle_secrets.py"
)
assert spec and spec.loader
sb = importlib.util.module_from_spec(spec)
sys.modules["scan_bundle_secrets"] = sb
spec.loader.exec_module(sb)

SECRET = "s3ss10n-s3cr3t-0123456789abcdef"
ENV = f"""# comment line
APP_ENV=local                      # local | prod
SESSION_SECRET={SECRET}
CRON_SECRET="quoted-cron-secret-value"
DATABASE_URL=postgresql+asyncpg://kt:pw@db:5432/kt
SMTP_PORT=587
EMPTY=
WEB_ORIGIN=https://kitchentab.example
VAPID_PUBLIC_KEY=BPublicKeyThatTheBrowserNeeds000
NEXT_PUBLIC_SCAN_ENABLED=true
"""


def make(tmp: Path, files: dict[str, str]) -> tuple[Path, Path]:
    env = tmp / ".env"
    env.write_text(ENV, encoding="utf-8")
    bundle = tmp / "static"
    for rel, text in files.items():
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    bundle.mkdir(exist_ok=True)
    return bundle, env


def run(bundle: Path, env: Path, *extra: str) -> int:
    code: int = sb.main(["--bundle", str(bundle), "--env", str(env), *extra])
    return code


@pytest.mark.id("SEC-07")
def test_parse_env_strips_quotes_and_comments() -> None:
    env = sb.parse_env(ENV)
    assert env["APP_ENV"] == "local"
    assert env["CRON_SECRET"] == "quoted-cron-secret-value"
    assert env["EMPTY"] == "" and env["SESSION_SECRET"] == SECRET


@pytest.mark.id("SEC-07")
def test_clean_bundle_passes_with_public_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle, env = make(
        tmp_path,
        {
            "chunks/app.js": 'const o="https://kitchentab.example";const k="BPublicKeyThatTheBrowserNeeds000";'
            "const s=process.env.NEXT_PUBLIC_SCAN_ENABLED;const port=587;const PROFILE_PAGE=1;",
            "css/app.css": ".a{color:red}",
        },
    )
    assert run(bundle, env) == 0
    assert "clean" in capsys.readouterr().out


@pytest.mark.id("SEC-07")
def test_server_secret_value_fails_without_printing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle, env = make(
        tmp_path,
        {
            "chunks/leak.js": f'var x="{SECRET}";',
            "chunks/db.js": "postgresql+asyncpg://kt:pw@db",
        },
    )
    assert run(bundle, env) == 1
    out = capsys.readouterr().out
    assert "the value of SESSION_SECRET" in out and "leak.js" in out
    assert SECRET not in out, "the scanner must never print the secret it found"


@pytest.mark.id("SEC-07")
def test_server_only_name_fails_but_longer_identifiers_do_not(tmp_path: Path) -> None:
    bundle, env = make(
        tmp_path,
        {"chunks/a.js": "const MY_SESSION_SECRET_LABEL=1;const CRON_SECRETS=2;"},
    )
    assert run(bundle, env) == 0
    (bundle / "chunks" / "b.js").write_text(
        "process.env.SESSION_SECRET", encoding="utf-8"
    )
    findings = sb.scan(bundle, *sb.load_env([env]))
    assert [(str(f.path).replace("\\", "/"), f.name, f.kind) for f in findings] == [
        ("chunks/b.js", "SESSION_SECRET", "name")
    ]


@pytest.mark.id("SEC-07")
def test_short_values_and_binary_files_are_ignored(tmp_path: Path) -> None:
    bundle, env = make(
        tmp_path,
        {"media/font.woff2": "local SESSION_SECRET", "chunks/a.js": "local 587"},
    )
    assert run(bundle, env) == 0


@pytest.mark.id("SEC-07")
def test_missing_bundle(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(ENV, encoding="utf-8")
    assert run(tmp_path / "nope", env) == 2
    assert run(tmp_path / "nope", env, "--allow-missing") == 0
