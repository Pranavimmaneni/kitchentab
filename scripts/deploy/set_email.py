"""`make set-email`: switch production sign-in codes from the test inbox to Gmail (BUILD_ALL C3,
§6 step 3; ADR-0016, ADR-0022).

Asks for the Gmail address and its app password (read without echo), stores SMTP_HOST,
SMTP_PORT, SMTP_USER, SMTP_PASSWORD and EMAIL_FROM with `vercel env add` (production and
preview), sets EMAIL_PROVIDER=smtp, removes TEST_INBOX_SECRET (which turns `/api/test/*` off),
redeploys, checks the test inbox is gone, then sends a real sign-in code to that address through
the production API and asks you to type it in.

    uv run --project apps/api python scripts/deploy/set_email.py [--dry-run] [--url https://...]

`--local` runs the same flow end to end on this machine without Vercel: it starts the API with
EMAIL_PROVIDER=smtp pointed at a STARTTLS relay in front of Mailpit (docker compose: SMTP 1025,
web 8025), so the real Gmail code path (STARTTLS, login, send) delivers the code to Mailpit.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from deploylib import (
    ENVIRONMENTS,
    EXIT_OK,
    Console,
    DeployError,
    Runner,
    Secret,
    Vercel,
    main_guard,
    mask_email,
    read_state,
    repo_root,
)
from redeploy import redeploy_live

TARGET = "set-email"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
APP_PASSWORD_RE = re.compile(r"^[a-z]{16}$")
APP_PASSWORDS_URL = "https://myaccount.google.com/apppasswords"
# Any STARTTLS relay works; Brevo's free plan (300 emails/day) needs no new Gmail account: verify your
# own address as a sender there and use the SMTP login and key from Settings -> SMTP & API.
PROVIDERS = {
    "gmail": ("smtp.gmail.com", 587),
    "brevo": ("smtp-relay.brevo.com", 587),
}
MAX_CODE_TRIES = 3


# ---------------------------------------------------------------------------- prompts


def ask_address(given: str | None) -> str:
    raw = given if given is not None else input("Gmail address: ")
    address = raw.strip().lower()
    if not EMAIL_RE.match(address):
        raise DeployError(
            "That doesn't look like an email address.",
            hint="Run `make set-email` again.",
        )
    return address


def ask_secret(prompt: str) -> Secret:
    """An SMTP key or password from a non-Gmail relay: read without echo, at least 8 characters."""
    if sys.stdin.isatty():
        raw = getpass.getpass(prompt)
    else:
        print(prompt, flush=True)
        raw = sys.stdin.readline()
    value = raw.strip()
    if len(value) < 8 or any(c.isspace() for c in value):
        raise DeployError(
            "That doesn't look like an SMTP key.", hint="Copy it again and re-run."
        )
    return Secret(value)


def ask_login(given: str | None, provider: str) -> str:
    example = (
        "e.g. 8a1b2c001@smtp-brevo.com"
        if provider == "brevo"
        else "the relay's user name"
    )
    raw = given if given is not None else input(f"SMTP login ({example}): ")
    login = raw.strip()
    if not login or any(c.isspace() for c in login):
        raise DeployError(
            "The SMTP login is empty.", hint="Copy it from the provider and re-run."
        )
    return login


def ask_app_password() -> Secret:
    prompt = "Gmail app password (16 letters; typing is hidden): "
    if sys.stdin.isatty():
        raw = getpass.getpass(prompt)
    else:  # piped (tests, scripts): read a line without echoing anything
        print(prompt, flush=True)
        raw = sys.stdin.readline()
    value = re.sub(r"\s+", "", raw).lower()
    if not APP_PASSWORD_RE.match(value):
        raise DeployError(
            "That isn't a Gmail app password (Google shows 16 letters, like 'abcd efgh ijkl mnop').",
            hint=f"Create one at {APP_PASSWORDS_URL} (2-Step Verification must be on), then run "
            "`make set-email` again. Never use your normal Google password here.",
        )
    return Secret(value)


def ask_code() -> str:
    print("Type the 6-digit code from that email:", flush=True)
    try:
        return input("> ").strip()
    except EOFError:
        return ""


# ---------------------------------------------------------------------------- talking to the API


class Api:
    """Just enough of a browser: a cookie jar and the double-submit CSRF header (ADR-0006)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf: str | None = None

    def call(
        self, method: str, path: str, body: dict[str, str] | None = None
    ) -> tuple[int, dict[str, object]]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.csrf:
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, _json(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, _json(err.read())
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise DeployError(
                f"Couldn't reach {self.base} ({getattr(err, 'reason', err)}).",
                hint="Check the URL and your connection, then run `make set-email` again.",
            ) from err

    def start(self) -> None:
        status, body = self.call("GET", "/api/v1/auth/csrf")
        if status != 200 or not isinstance(body.get("token"), str):
            raise DeployError(
                f"{self.base}/api/v1/auth/csrf answered {status}; is the API up?"
            )
        self.csrf = str(body["token"])


def _json(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def check_sign_in(
    console: Console,
    base: str,
    address: str,
    read_code: Callable[[], str],
    *,
    where: str,
) -> None:
    """Test inbox is off, a real code arrives, and it signs in."""
    api = Api(base)
    status, _ = api.call("GET", "/api/test/inbox")
    if status != 404:
        raise DeployError(
            f"{base}/api/test/inbox answered {status}, so the test inbox is still on.",
            hint="The new settings didn't reach the running deployment. Run `make redeploy`, then `make set-email`.",
        )
    console.say("Test inbox is off (/api/test/inbox -> 404).")
    api.start()
    status, body = api.call(
        "POST", "/api/v1/auth/otp/send", {"channel": "email", "destination": address}
    )
    if status == 429:
        raise DeployError(
            "Too many codes were sent to that address in the last hour.",
            hint="Wait an hour and retry.",
        )
    if status != 200:
        raise DeployError(
            f"The API couldn't send the email (HTTP {status}: {body.get('detail') or body.get('title') or 'no detail'}).",
            hint="Usually the app password is wrong or was revoked, or 2-Step Verification is off. Check the "
            f"function logs (vercel logs), make a new app password at {APP_PASSWORDS_URL}, run `make set-email` again.",
        )
    console.say(f"Sent a sign-in code to {mask_email(address)}. {where}")
    for attempt in range(1, MAX_CODE_TRIES + 1):
        code = read_code()
        if not re.fullmatch(r"\d{6}", code):
            console.say("That isn't 6 digits.")
            continue
        status, body = api.call(
            "POST",
            "/api/v1/auth/otp/verify",
            {"channel": "email", "destination": address, "code": code},
        )
        if status == 200:
            new = " (this also created your account)" if body.get("is_new") else ""
            console.say(f"Signed in with the emailed code{new}. Email delivery works.")
            return
        console.say(str(body.get("detail") or body.get("title") or f"HTTP {status}"))
        if body.get("code") == "CODE_LOCKED" or attempt == MAX_CODE_TRIES:
            break
    raise DeployError(
        "The code was never confirmed.",
        hint="If the email didn't arrive, look in Spam; then run `make set-email` again for a fresh code.",
    )


# ---------------------------------------------------------------------------- production


def smtp_values(
    address: str,
    password: Secret,
    host: str = "smtp.gmail.com",
    port: int = 587,
    login: str | None = None,
) -> list[tuple[str, str | Secret, str]]:
    """(name, value, how to show it). EMAIL_PROVIDER goes last so a half-finished run never
    leaves production pointing at a relay without credentials. `login` defaults to the address
    (Gmail); relays such as Brevo log in with their own user name."""
    shown = mask_email(address)
    user = login or address
    return [
        ("SMTP_HOST", host, host),
        ("SMTP_PORT", str(port), str(port)),
        (
            "SMTP_USER",
            user,
            f"<email: {mask_email(user)}>" if "@" in user else "<smtp login>",
        ),
        ("SMTP_PASSWORD", password, f"<app password: {len(password)} chars>"),
        ("EMAIL_FROM", f"KitchenTab <{address}>", f"KitchenTab <{shown}>"),
        ("EMAIL_PROVIDER", "smtp", "smtp"),
    ]


def production(console: Console, runner: Runner, args: argparse.Namespace) -> int:
    vercel = Vercel(runner)
    dry = runner.dry_run
    console.say(
        f"KitchenTab set-email{'  [dry run: printing commands, running none]' if dry else ''}"
    )
    runner.announce_vercel()
    vercel.require_linked()
    vercel.whoami()
    origin = (args.url or read_state(runner.root).get("production_url") or "").rstrip(
        "/"
    )
    if not origin:
        if not dry:
            raise DeployError(
                "I don't know the production URL yet.",
                hint="Run `make deploy` first, or pass it: `make set-email ARGS=--url=https://kitchentab.vercel.app`.",
            )
        origin = "https://kitchentab.vercel.app"
    _check_origin(origin)

    provider = args.provider
    host, port = PROVIDERS.get(provider, (args.smtp_host, args.smtp_port))
    if args.smtp_host:
        host = args.smtp_host
    if args.smtp_port:
        port = args.smtp_port
    if not host:
        raise DeployError(
            "--provider other needs --smtp-host (and usually --smtp-port)."
        )
    login: str | None = None
    if dry:
        address, password = "gmail-address@gmail.com", Secret("x" * 16)
    elif provider == "gmail":
        console.say(
            f"Gmail sign-in codes for {origin}. You need the address and an app password ({APP_PASSWORDS_URL})."
        )
        address = ask_address(args.address)
        password = ask_app_password()
    else:
        console.say(
            f"Sign-in codes for {origin} through {host}:{port}. You need the sender address you "
            "verified with the provider, its SMTP login and its SMTP key."
        )
        address = ask_address(args.address)
        login = ask_login(args.smtp_login, provider)
        password = ask_secret("SMTP key (typing is hidden): ")
    console.register(password)

    console.step("1. Store the Gmail settings (production and preview)")
    for env in ENVIRONMENTS:
        names = set() if dry else vercel.env_names(env)
        for name, value, shown in smtp_values(address, password, host, port, login):
            if name in names:
                vercel.env_rm(name, env)
            vercel.env_add(name, env, value, note=shown)
        if dry or "TEST_INBOX_SECRET" in names:
            vercel.env_rm("TEST_INBOX_SECRET", env)

    console.step("2. Redeploy production with the new settings")
    redeploy_live(console, vercel, origin)

    console.step("3. Send a real code and sign in with it")
    if dry:
        for line in (
            f"GET  {origin}/api/test/inbox  (expect 404)",
            f"GET  {origin}/api/v1/auth/csrf",
            f"POST {origin}/api/v1/auth/otp/send  {{channel: email, destination: <gmail address>}}",
            "ask for the 6-digit code",
            f"POST {origin}/api/v1/auth/otp/verify  {{channel: email, destination, code}}",
        ):
            console.say(f"[dry-run] {line}")
        return EXIT_OK
    check_sign_in(
        console, origin, address, ask_code, where="Check that inbox (and Spam)."
    )
    console.say(f"\nDone: production now sends sign-in codes through {host}.")
    return EXIT_OK


def _check_origin(origin: str) -> None:
    host = urlsplit(origin).hostname or ""
    if not (origin.startswith("https://") or host in ("127.0.0.1", "localhost")):
        raise DeployError(f"The production URL must be https (got {origin}).")


# ---------------------------------------------------------------------------- local (Mailpit)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class LocalApi:
    """The real FastAPI app on a free port, served by uvicorn in a thread of this process (so
    nothing is left running if the check fails). `overrides` go into os.environ first."""

    def __init__(self, api_dir: Path, overrides: dict[str, str]):
        os.environ.update(overrides)
        if str(api_dir) not in sys.path:
            sys.path.insert(0, str(api_dir))
        import uvicorn
        from app.settings import get_settings

        get_settings.cache_clear()
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        os.environ["WEB_ORIGIN"] = os.environ["PUBLIC_LINK_BASE"] = self.base
        config = uvicorn.Config(
            "app.main:create_app",
            factory=True,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> Self:
        self.thread.start()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and self.thread.is_alive():
            try:
                with urllib.request.urlopen(
                    self.base + "/api/v1/health", timeout=2
                ) as resp:
                    if resp.status == 200:
                        return self
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.3)
        self.__exit__()
        raise DeployError(
            "The local API didn't start (see the log above).",
            hint="Is Postgres up (`make up`) and migrated (`make migrate`)?",
        )

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


def local(console: Console, runner: Runner, args: argparse.Namespace) -> int:
    from smtp_relay import StartTlsRelay, upstream_reachable

    dry = runner.dry_run
    console.say(f"KitchenTab set-email --local (Mailpit){'  [dry run]' if dry else ''}")
    smtp_host, _, smtp_port = args.mailpit_smtp.partition(":")
    upstream = (smtp_host or "localhost", int(smtp_port or 1025))
    api_dir = runner.root / "apps" / "api"
    if dry:
        console.say(
            f"[dry-run] start a STARTTLS relay on 127.0.0.1:<port> -> Mailpit {upstream[0]}:{upstream[1]}"
        )
        console.say(
            "[dry-run] serve apps/api (uvicorn, in-process) with EMAIL_PROVIDER=smtp SMTP_HOST=localhost "
            "SMTP_PORT=<relay> SMTP_USER=<address> SMTP_PASSWORD=<app password>"
        )
        console.say(
            "[dry-run] GET /api/test/inbox (404), POST /api/v1/auth/otp/send, ask for the code, POST /otp/verify"
        )
        return EXIT_OK
    if not upstream_reachable(*upstream):
        raise DeployError(
            f"Mailpit isn't listening on {upstream[0]}:{upstream[1]}.",
            hint="Start it with `make up`.",
        )
    address = ask_address(args.address)
    password = ask_app_password()
    console.register(password)
    with (
        tempfile.TemporaryDirectory(prefix="kt-set-email-") as tmp,
        StartTlsRelay(Path(tmp), upstream, address, password.reveal()) as relay,
    ):
        overrides = {
            "APP_ENV": "local",
            "PROFILE": "vercel",  # no Redis needed
            "QUEUE_PROVIDER": "inline",
            "TEST_ROUTES_ENABLED": "false",
            "EMAIL_PROVIDER": "smtp",
            "SMTP_HOST": "localhost",
            "SMTP_PORT": str(relay.port),
            "SMTP_USER": address,
            "SMTP_PASSWORD": password.reveal(),
            "EMAIL_FROM": f"KitchenTab <{address}>",
            "SSL_CERT_FILE": str(
                relay.cert_path
            ),  # trust the relay's throwaway certificate
        }
        with LocalApi(api_dir, overrides) as api:
            console.say(
                f"Local API (EMAIL_PROVIDER=smtp) is up at {api.base}; STARTTLS relay on 127.0.0.1:{relay.port} "
                f"-> Mailpit {upstream[0]}:{upstream[1]}."
            )
            check_sign_in(
                console,
                api.base,
                address,
                ask_code,
                where=f"Read it in Mailpit: {args.mailpit_url}",
            )
        if relay.relayed < 1 or not any(relay.auth_results):
            raise DeployError(
                "The API never logged in to the relay with the app password."
            )
    console.say(
        "\nLocal check passed: STARTTLS, login and delivery through Mailpit all work."
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Switch sign-in codes to Gmail SMTP.")
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, run none"
    )
    parser.add_argument("--address", help="sender address (asked for if missing)")
    parser.add_argument(
        "--provider",
        choices=[*PROVIDERS, "other"],
        default="gmail",
        help="gmail (app password), brevo (free 300/day, no Gmail needed) or other (--smtp-host)",
    )
    parser.add_argument("--smtp-host", help="relay host (other providers)")
    parser.add_argument(
        "--smtp-port", type=int, help="relay port, STARTTLS (default 587)"
    )
    parser.add_argument(
        "--smtp-login", help="relay login if it isn't the address (asked for)"
    )
    parser.add_argument(
        "--url", help="production origin (default: the one `make deploy` recorded)"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="end-to-end check against Mailpit, no Vercel",
    )
    parser.add_argument(
        "--mailpit-smtp",
        default="localhost:1025",
        help="Mailpit SMTP host:port (--local)",
    )
    parser.add_argument(
        "--mailpit-url",
        default="http://localhost:8025",
        help="Mailpit web UI (--local)",
    )
    args = parser.parse_args(argv)
    console = Console(args.dry_run)
    runner = Runner(console, repo_root(), TARGET)
    body = local if args.local else production
    return main_guard(console, lambda: body(console, runner, args))


if __name__ == "__main__":
    sys.exit(main())
