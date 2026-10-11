"""Shared plumbing for the morning deploy scripts (BUILD_ALL §6 step 3, ADR-0022).

Python 3.12 standard library only. Every script:

* resolves the `vercel` CLI with `find_vercel_cli` (skipping the Python `vercel` package's console
  script in a virtualenv; `VERCEL_CLI` overrides) and prints the path it will use;
* echoes every command before running it, and with `--dry-run` only echoes;
* never prints a secret: values go to `vercel env add` on stdin (never argv), are wrapped in
  `Secret`, and every line of output is passed through `Console.redact`;
* turns CLI failures into one clear message and a non-zero exit code.

`KITCHENTAB_ROOT` overrides the repo root (the stubbed-CLI tests point it at a temp folder).
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

PROJECT = "kitchentab"
# Vercel CLI 54 can't add a Preview variable without a Git branch or the value on argv, so a project
# without Git (deployed from this folder) sets Production only: KT_VERCEL_ENVIRONMENTS=production.
ENVIRONMENTS = tuple(
    e.strip()
    for e in os.environ.get("KT_VERCEL_ENVIRONMENTS", "production,preview").split(",")
    if e.strip()
)
BLOB_STORE = "kitchentab-receipts"
# Accounts created by launch-check / e2e against production use this reserved (RFC 2606 `.test`)
# domain; `make prod-clean-test-data` deletes only data reachable from these users (ADR-0022).
TEST_EMAIL_DOMAIN = "launch-check.kitchentab.test"
STATE_FILE = Path(".vercel") / "kitchentab-deploy.json"
GOOGLE_CALLBACK_PATH = "/api/v1/auth/google/callback"
# Pulled values that are not secret (everything else pulled is redacted from output).
PUBLIC_SETTINGS = frozenset(
    {
        "APP_ENV",
        "PROFILE",
        "WEB_ORIGIN",
        "PUBLIC_LINK_BASE",
        "GOOGLE_REDIRECT_URI",
        "GOOGLE_ISSUER",
        "VAPID_SUBJECT",
        "VAPID_PUBLIC_KEY",
        "SMTP_HOST",
        "SMTP_PORT",
        "PGHOST",
        "PGHOST_UNPOOLED",
        "PGDATABASE",
        "POSTGRES_HOST",
        "POSTGRES_DATABASE",
        "VERCEL_ENV",
        "VERCEL_URL",
        "VERCEL_TARGET_ENV",
    }
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_ACTION_NEEDED = 3


# ---------------------------------------------------------------------------- errors, secrets


class DeployError(Exception):
    """A failure with a message for Pranav and, usually, what to do next."""

    def __init__(
        self,
        message: str,
        hint: str | None = None,
        code: int = EXIT_FAIL,
        tail: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        self.tail = tail  # last lines of the failing command's output, already redacted

    def wrap(self, message: str, hint: str | None = None) -> DeployError:
        """Same failure, with a message that says what it means for this script."""
        detail = self.message if not self.hint else f"{self.message}\n{self.hint}"
        return DeployError(
            message,
            hint=f"{detail}\n{hint}" if hint else detail,
            code=self.code,
            tail=self.tail,
        )


class Secret:
    """A value that must never be printed. `str()` and `repr()` show only its length."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __len__(self) -> int:
        return len(self._value)

    def __str__(self) -> str:
        return f"<secret: {len(self._value)} chars>"

    __repr__ = __str__


def new_secret(nbytes: int) -> Secret:
    """URL-safe random secret; `nbytes` of entropy, about 1.33 characters per byte."""
    return Secret(secrets.token_urlsafe(nbytes))


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


# ---------------------------------------------------------------------------- VAPID (P-256)

_P = 2**256 - 2**224 + 2**192 + 2**96 - 1
_A = _P - 3
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)


def _ec_add(
    p: tuple[int, int] | None, q: tuple[int, int] | None
) -> tuple[int, int] | None:
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % _P == 0:
        return None
    if p == q:
        slope = (3 * p[0] * p[0] + _A) * pow(2 * p[1], -1, _P) % _P
    else:
        slope = (q[1] - p[1]) * pow(q[0] - p[0], -1, _P) % _P
    x = (slope * slope - p[0] - q[0]) % _P
    return x, (slope * (p[0] - x) - p[1]) % _P


def _ec_mul(k: int, point: tuple[int, int]) -> tuple[int, int]:
    result: tuple[int, int] | None = None
    addend: tuple[int, int] | None = point
    while k:
        if k & 1:
            result = _ec_add(result, addend)
        addend = _ec_add(addend, addend)
        k >>= 1
    assert result is not None
    return result


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def vapid_public_from_private(private_b64url: str) -> str:
    padded = private_b64url + "=" * (-len(private_b64url) % 4)
    d = int.from_bytes(base64.urlsafe_b64decode(padded), "big")
    x, y = _ec_mul(d, _G)
    return _b64url(b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big"))


def new_vapid_keys() -> tuple[str, Secret]:
    """(public, private) in the raw base64url form pywebpush and browsers use: the public key is
    the 65-byte uncompressed P-256 point, the private key the 32-byte scalar."""
    d = secrets.randbelow(_N - 1) + 1
    private = _b64url(d.to_bytes(32, "big"))
    return vapid_public_from_private(private), Secret(private)


# ---------------------------------------------------------------------------- output


class Console:
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self._secrets: set[str] = set()

    def register(self, value: str | Secret | None) -> None:
        raw = value.reveal() if isinstance(value, Secret) else value
        if raw and len(raw) >= 6:
            self._secrets.add(raw)

    def redact(self, text: str) -> str:
        for value in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(value, f"<secret: {len(value)} chars>")
        return text

    def say(self, message: str = "") -> None:
        print(self.redact(message), flush=True)

    def step(self, title: str) -> None:
        self.say(f"\n== {title}")

    def warn(self, message: str) -> None:
        print(self.redact(f"WARNING: {message}"), file=sys.stderr, flush=True)

    def error(self, err: DeployError) -> None:
        if err.tail:
            print("--- last lines of output ---", file=sys.stderr)
            print(self.redact(err.tail), file=sys.stderr)
            print("---", file=sys.stderr, flush=True)
        print(self.redact(f"\nERROR: {err.message}"), file=sys.stderr, flush=True)
        if err.hint:
            for line in err.hint.splitlines():
                print(self.redact(f"  {line}"), file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------- running commands


def display_arg(arg: str) -> str:
    """Shell-quote only when needed, so placeholders like <url> stay readable."""
    return arg if re.fullmatch(r"[\w@%+=:,./<>-]+", arg) else shlex.quote(arg)


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return f"{self.out}\n{self.err}"


def repo_root() -> Path:
    override = os.environ.get("KITCHENTAB_ROOT")
    return Path(override).resolve() if override else Path(__file__).resolve().parents[2]


_LOGIN = re.compile(
    r"no existing credentials|not logged in|vercel login|token is not valid|"
    r"invalid token|not authorized|authentication required",
    re.IGNORECASE,
)
_NETWORK = re.compile(
    r"ENOTFOUND|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|fetch failed|socket hang up|"
    r"network (?:error|problem)|getaddrinfo",
    re.IGNORECASE,
)


VERCEL_CLI_ENV = "VERCEL_CLI"


def _is_venv_scripts_dir(directory: Path) -> bool:
    """A virtualenv's Scripts (Windows) or bin folder: the running interpreter's, or any folder whose
    parent holds a `pyvenv.cfg`."""
    try:
        resolved = directory.resolve()
    except OSError:
        return False
    prefixes = {sys.prefix, sys.exec_prefix, os.environ.get("VIRTUAL_ENV") or ""}
    for prefix in filter(None, prefixes):
        try:
            if resolved.parent == Path(prefix).resolve():
                return True
        except OSError:
            continue
    return (resolved.parent / "pyvenv.cfg").is_file()


def find_vercel_cli(path: str | None = None) -> str | None:
    """The Vercel CLI (npm `vercel`), never the Python `vercel` package's console script.

    `uv run` puts the API venv's Scripts folder first on PATH, and the Vercel Queues SDK dependency
    installs a `vercel` executable there, so plain `shutil.which` finds the wrong program. Folders
    that belong to a virtualenv are skipped; `VERCEL_CLI=<path>` picks the CLI explicitly.
    """
    override = os.environ.get(VERCEL_CLI_ENV)
    if override:
        found = shutil.which(override) or (
            override if Path(override).is_file() else None
        )
        if found is None:
            raise DeployError(
                f"{VERCEL_CLI_ENV} is set to {override}, but there is no program there.",
                hint=f"Point {VERCEL_CLI_ENV} at the Vercel CLI (`where vercel` / `which vercel` in "
                "a normal terminal), or unset it to search PATH.",
            )
        return os.path.abspath(found)
    search = os.environ.get("PATH", os.defpath) if path is None else path
    for entry in search.split(os.pathsep):
        if not entry or _is_venv_scripts_dir(Path(entry)):
            continue
        found = shutil.which("vercel", path=entry)
        if found:
            return found
    return None


class Runner:
    """Runs (or, in dry-run, only prints) external commands from the repo root."""

    def __init__(self, console: Console, root: Path, target: str):
        self.console = console
        self.root = root
        self.target = target  # the make target, for "re-run `make <target>`" hints
        self.dry_run = console.dry_run

    def which(self, tool: str) -> str | None:
        return find_vercel_cli() if tool == "vercel" else shutil.which(tool)

    def announce_vercel(self) -> str | None:
        path = self.which("vercel")
        self.console.say(f"vercel: {path or 'not found on PATH'}")
        if path is None and not self.dry_run:
            raise DeployError(
                "The Vercel CLI isn't installed (no `vercel` on PATH outside a virtualenv).",
                hint="Install it with `npm i -g vercel`, run `vercel login`, then re-run "
                f"`make {self.target}` (or set {VERCEL_CLI_ENV} to the CLI's path).",
            )
        return path

    def show(
        self,
        tool: str,
        args: Iterable[str],
        *,
        stdin_note: str | None = None,
        env_note: str | None = None,
        cwd: Path | None = None,
    ) -> None:
        line = " ".join(display_arg(a) for a in [tool, *args])
        if env_note:
            line = f"{env_note} {line}"
        if cwd is not None and cwd != self.root:
            rel = (
                cwd.relative_to(self.root).as_posix()
                if cwd.is_relative_to(self.root)
                else str(cwd)
            )
            line = f"(cd {rel} && {line})"
        if stdin_note:
            line = f"{line} < {stdin_note}"
        prefix = "[dry-run] $ " if self.dry_run else "$ "
        self.console.say(prefix + line)

    def run(
        self,
        tool: str,
        args: list[str],
        *,
        what: str,
        stdin: str | Secret | None = None,
        stdin_note: str | None = None,
        capture: bool = True,
        stream_stderr: bool = False,
        interactive: bool = False,
        check: bool = True,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        env_note: str | None = None,
    ) -> Result:
        """`what` completes "Could not ..." in the failure message.

        capture: collect stdout and stderr (redacted on failure). stream_stderr: collect stdout
        only and let stderr (build logs) reach the terminal. interactive: inherit the terminal.
        """
        if stdin is not None and stdin_note is None:
            stdin_note = str(stdin) if isinstance(stdin, Secret) else "<value>"
        self.show(tool, args, stdin_note=stdin_note, env_note=env_note, cwd=cwd)
        if self.dry_run:
            return Result(0, "", "")
        exe = self.which(tool)
        if exe is None:
            raise DeployError(f"`{tool}` is not on PATH, so I could not {what}.")
        raw_stdin = stdin.reveal() if isinstance(stdin, Secret) else stdin
        full_env = None
        if env is not None:
            full_env = {**os.environ, **env}
        try:
            if interactive:
                proc = subprocess.run(
                    [exe, *args], cwd=cwd or self.root, env=full_env, check=False
                )
                result = Result(proc.returncode, "", "")
            else:
                proc = subprocess.run(
                    [exe, *args],
                    input=raw_stdin,
                    stdin=None if raw_stdin is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE if (capture or stream_stderr) else None,
                    stderr=subprocess.PIPE if capture and not stream_stderr else None,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=cwd or self.root,
                    env=full_env,
                    check=False,
                )
                result = Result(proc.returncode, proc.stdout or "", proc.stderr or "")
        except OSError as exc:
            raise DeployError(f"Could not {what}: {exc}") from exc
        if check and result.code != 0:
            raise self.failure(tool, args, what, result)
        return result

    def failure(
        self, tool: str, args: list[str], what: str, result: Result
    ) -> DeployError:
        command = shlex.join([tool, *args[:3]])
        text = result.text
        rerun = f"re-run `make {self.target}`"
        if tool == "vercel" and _LOGIN.search(text):
            return DeployError(
                "You're not logged in to Vercel.",
                hint=f"Run `vercel login` (it opens the browser), then {rerun}.",
            )
        if _NETWORK.search(text):
            return DeployError(
                f"Network problem while trying to {what} (`{command}`).",
                hint=f"Check your internet connection, then {rerun}: it skips whatever is already done.",
            )
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        detail = (
            self.console.redact(lines[-1].strip()) if lines else "see the output above"
        )
        return DeployError(
            f"Could not {what}: `{command}` exited with {result.code}: {detail}",
            hint=f"Fix the problem, then {rerun}.",
            tail=self.console.redact("\n".join(lines[-15:]))
            if len(lines) > 1
            else None,
        )


# ---------------------------------------------------------------------------- parsing CLI output

_ENV_ROW = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s+\S")
_URL = re.compile(r"https?://[A-Za-z0-9.\-:]+(?:/[^\s]*)?")


def parse_env_ls(text: str) -> set[str]:
    """Variable names from `vercel env ls <environment>` (the table's first column)."""
    names = set()
    for line in text.splitlines():
        m = _ENV_ROW.match(line)
        if m:
            names.add(m.group(1))
    return names


def parse_dotenv(text: str) -> dict[str, str]:
    """The `KEY="value"` file written by `vercel env pull`."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = (
                    value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
                )
        values[key] = value
    return values


def last_url(text: str) -> str | None:
    """The last deployment URL printed. CLI 54 also prints JSON with api.vercel.com links (and a stray
    quote), which are never the deployment."""
    urls = [u.rstrip('/"') for u in _URL.findall(text)]
    deployments = [
        u
        for u in urls
        if not u.startswith(("https://api.vercel.com", "https://vercel.com"))
    ]
    return deployments[-1] if deployments else None


def parse_inspect(text: str) -> tuple[str | None, list[str]]:
    """(deployment url, aliases) from `vercel inspect <url>`."""
    url = None
    aliases: list[str] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        heading = re.fullmatch(r"[A-Z][A-Za-z ]+", stripped)
        if heading and line.startswith("  ") and not line.startswith("    "):
            section = stripped.lower()
            continue
        if section == "general" and url is None:
            m = re.match(r"url\s+(\S+)", stripped)
            if m:
                found = m.group(1)
                url = found if found.startswith("http") else f"https://{found}"
        elif section == "aliases":
            aliases += [u.rstrip("/") for u in _URL.findall(stripped)]
    return url, aliases


def choose_production_origin(aliases: list[str]) -> str | None:
    """A custom domain if there is one, else the shortest *.vercel.app alias."""
    https = [a for a in aliases if a.startswith("https://")]
    custom = [a for a in https if not a.endswith(".vercel.app")]
    pool = custom or https
    return min(pool, key=len) if pool else None


@dataclass
class DeploymentRow:
    age: str
    url: str
    status: str
    environment: str


def parse_deployments(text: str) -> list[DeploymentRow]:
    """Rows of `vercel ls <project>`, newest first."""
    rows = []
    for line in text.splitlines():
        m = re.match(
            r"^\s*(\S+)\s+(https://\S+)\s+(?:\W\s*)?([A-Za-z]+)\s+([A-Za-z]+)", line
        )
        if m:
            rows.append(
                DeploymentRow(
                    m.group(1), m.group(2).rstrip("/"), m.group(3), m.group(4)
                )
            )
    return rows


# ---------------------------------------------------------------------------- database URLs


def _neon_query(url: str) -> tuple[SplitResult, list[tuple[str, str]]]:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return parts, query


def asyncpg_sqlalchemy_url(url: str) -> str:
    """Neon's `postgresql://...?sslmode=require&channel_binding=require` as SQLAlchemy+asyncpg
    expects it (`postgresql+asyncpg://...?ssl=require`); asyncpg rejects the libpq-only options."""
    parts, query = _neon_query(url)
    query = [("ssl", v) if k == "sslmode" else (k, v) for k, v in query]
    return urlunsplit(
        ("postgresql+asyncpg", parts.netloc, parts.path, urlencode(query), "")
    )


def libpq_url(url: str) -> str:
    """A DSN asyncpg.connect() accepts: plain scheme, `sslmode` kept, `channel_binding` dropped."""
    parts, query = _neon_query(url)
    return urlunsplit(("postgresql", parts.netloc, parts.path, urlencode(query), ""))


def url_password(url: str) -> str | None:
    return urlsplit(url).password


DIRECT_URL_KEYS = (
    "DATABASE_DIRECT_URL",
    "DATABASE_URL_UNPOOLED",
    "POSTGRES_URL_NON_POOLING",
)


def direct_database_url(env: Mapping[str, str]) -> str:
    """Neon's direct (unpooled) URL: migrations and bulk deletes can't go through PgBouncer."""
    for key in DIRECT_URL_KEYS:
        if env.get(key):
            return env[key]
    fallback = os.environ.get("NEON_DIRECT_URL")
    if fallback:
        return fallback
    raise DeployError(
        "Couldn't find Neon's direct database URL in the production environment "
        f"(looked for {', '.join(DIRECT_URL_KEYS)}).",
        hint="Is the Neon integration connected to the project? `vercel env ls production` should "
        "list DATABASE_URL_UNPOOLED.\nIf it is marked Sensitive (so `vercel env pull` can't read it), "
        "copy the direct URL from the Neon console and run with NEON_DIRECT_URL set in your shell.",
    )


# ---------------------------------------------------------------------------- Vercel helpers


class Vercel:
    def __init__(self, runner: Runner):
        self.runner = runner
        self.console = runner.console

    def run(self, args: list[str], *, what: str, **kw: object) -> Result:
        return self.runner.run("vercel", args, what=what, **kw)  # type: ignore[arg-type]

    def whoami(self) -> str:
        res = self.run(["whoami"], what="check who is logged in to Vercel")
        user = res.out.strip().splitlines()[-1] if res.out.strip() else ""
        if user and not self.runner.dry_run:
            self.console.say(f"Logged in to Vercel as {user}.")
        return user

    def linked_project(self) -> str | None:
        path = self.runner.root / ".vercel" / "project.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        name = data.get("projectName") or data.get("projectId")
        return str(name) if name else None

    def require_linked(self) -> None:
        if self.runner.dry_run:
            return
        if self.linked_project() is None:
            raise DeployError(
                "This folder isn't linked to a Vercel project yet.",
                hint="Run `make deploy-setup` first.",
            )

    def env_names(self, environment: str) -> set[str]:
        res = self.run(
            ["env", "ls", environment],
            what=f"list the {environment} environment variables",
        )
        return parse_env_ls(res.text)

    def env_add(
        self,
        name: str,
        environment: str,
        value: str | Secret,
        *,
        note: str | None = None,
    ) -> None:
        # Production and Preview values are sensitive by default (CLI 54) and then read back empty
        # from `vercel env pull`. Only secrets stay sensitive; plain settings (APP_ENV, providers,
        # the public VAPID key, origins) stay readable so the scripts can check them.
        argv = ["env", "add", name, environment]
        if not isinstance(value, Secret):
            argv.append("--no-sensitive")
        try:
            self.run(
                argv,
                what=f"set {name} for {environment}",
                stdin=value,
                stdin_note=note
                or (str(value) if isinstance(value, Secret) else "<value>"),
            )
        except DeployError as err:
            if "already" in err.message.lower():
                raise DeployError(
                    f"Vercel says {name} already exists in {environment}, but `vercel env ls "
                    f"{environment}` didn't list it.",
                    hint=f"Check `vercel env ls {environment}` (or the dashboard: Settings -> "
                    f"Environment Variables), remove the stray {name} if it is wrong, then "
                    f"re-run `make {self.runner.target}`.",
                ) from err
            raise

    def env_rm(self, name: str, environment: str) -> None:
        self.run(
            ["env", "rm", name, environment, "--yes"],
            what=f"remove {name} from {environment}",
        )

    def env_pull(self, environment: str) -> dict[str, str]:
        """Pull into a temp file outside the repo, read it, delete it. Values are registered as
        secrets so nothing pulled can be echoed."""
        tmpdir = Path(tempfile.mkdtemp(prefix="kt-env-"))
        path = tmpdir / f".env.{environment}"
        try:
            self.run(
                ["env", "pull", str(path), f"--environment={environment}", "--yes"],
                what=f"read the {environment} environment variables",
            )
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        values = parse_dotenv(text)
        for key, value in values.items():
            if key not in PUBLIC_SETTINGS and not key.endswith("_PROVIDER"):
                self.console.register(value)
            password = url_password(value) if "://" in value else None
            self.console.register(password)
        return values

    def deploy_prod(self) -> str:
        res = self.run(
            ["deploy", "--prod"],
            what="deploy to production",
            stream_stderr=True,
        )
        return self._deployment_url(res, "https://<new-deployment>.vercel.app")

    def redeploy(self, deployment_url: str) -> str:
        res = self.run(
            ["redeploy", deployment_url],
            what="redeploy production",
            stream_stderr=True,
        )
        return self._deployment_url(res, "https://<redeployed>.vercel.app")

    def _deployment_url(self, res: Result, placeholder: str) -> str:
        if self.runner.dry_run:
            return placeholder
        url = last_url(res.out)
        if not url:
            raise DeployError(
                "The deploy finished but I couldn't find its URL in the CLI output."
            )
        return url

    def inspect(self, url: str, *, check: bool = True) -> tuple[str | None, list[str]]:
        res = self.run(["inspect", url], what=f"inspect {url}", check=check)
        if res.code != 0:
            return None, []
        return parse_inspect(res.text)

    def deployments(self) -> list[DeploymentRow]:
        res = self.run(["ls", PROJECT], what="list recent deployments")
        return parse_deployments(res.text)

    def current_production(self, origin: str | None) -> str:
        """The deployment the production domain points at now (after a rollback that is not
        the newest one), falling back to the newest ready production deployment."""
        if self.runner.dry_run:
            return "https://<current-production-deployment>.vercel.app"
        if origin:
            url, _ = self.inspect(origin, check=False)
            if url:
                return url
        for row in self.deployments():
            if (
                row.environment.lower() == "production"
                and row.status.lower() == "ready"
            ):
                return row.url
        raise DeployError(
            "There is no ready production deployment yet.",
            hint="Run `make deploy` first.",
        )


# ---------------------------------------------------------------------------- local state


def read_state(root: Path) -> dict[str, str]:
    try:
        data = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def write_state(root: Path, **values: str) -> None:
    path = root / STATE_FILE
    data = read_state(root)
    data.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------- entry point helper


def main_guard(console: Console, body: Callable[[], int]) -> int:
    try:
        return body()
    except DeployError as err:
        console.error(err)
        return err.code
    except KeyboardInterrupt:
        console.say(
            "\nStopped. Re-run the same command when ready: finished steps are skipped."
        )
        return 130
