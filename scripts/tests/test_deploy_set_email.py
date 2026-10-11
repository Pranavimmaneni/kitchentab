"""VERCEL-10 / AUTH-15: `make set-email` against the stubbed Vercel CLI and a fake production API,
plus the local end-to-end mode through the real API and Mailpit (ADR-0016, ADR-0022)."""

from __future__ import annotations

import json
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from deploy_harness import DEPLOY, ROOT, Harness

SCRIPT = "set_email.py"
ADDRESS = "kitchentab.sender@example.com"
APP_PASSWORD = "abcd efgh ijkl mnop"
CODE = "246810"


class FakeProd:
    """The production API as the redeployed app behaves: it reads which email provider the live
    deployment was built with from the fake Vercel state."""

    def __init__(self, harness: Harness):
        self.harness = harness
        self.sent: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def reply(
                self, status: int, body: dict, headers: dict[str, str] | None = None
            ) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def live_provider(self) -> str | None:
                state = fake.harness.state()
                live = next(
                    d for d in state["deployments"] if d["url"] == state["current"]
                )
                return live.get("email_provider")

            def do_GET(self) -> None:
                if self.path.startswith("/api/test/inbox"):
                    on = self.live_provider() == "testinbox"
                    self.reply(401 if on else 404, {"title": "Not found"})
                elif self.path == "/api/v1/auth/csrf":
                    self.reply(
                        200,
                        {"token": "tok123"},
                        {"Set-Cookie": "kt_csrf=tok123; Path=/"},
                    )
                else:
                    self.reply(404, {})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.headers.get(
                    "X-CSRF-Token"
                ) != "tok123" or "kt_csrf=tok123" not in self.headers.get("Cookie", ""):
                    self.reply(403, {"code": "CSRF_FAILED", "detail": "csrf"})
                elif self.path == "/api/v1/auth/otp/send":
                    if self.live_provider() != "smtp":
                        self.reply(500, {"detail": "smtp not configured"})
                        return
                    fake.sent.append(body["destination"])
                    self.reply(
                        200,
                        {
                            "sent": True,
                            "masked": "p***@gmail.com",
                            "resend_after_seconds": 30,
                        },
                    )
                elif self.path == "/api/v1/auth/otp/verify":
                    if body["code"] == CODE:
                        self.reply(
                            200,
                            {"user_id": "u1", "display_name": "Pranav", "is_new": True},
                        )
                    else:
                        self.reply(
                            400,
                            {
                                "code": "CODE_MISMATCH",
                                "title": "That code didn't match. 4 tries left.",
                            },
                        )
                else:
                    self.reply(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def prod(harness: Harness) -> Iterator[FakeProd]:
    fake = FakeProd(harness)
    harness.provision(origin=fake.origin)
    state = harness.state()
    for d in state["deployments"]:
        d["email_provider"] = "testinbox"
    harness.save_state(state)
    yield fake
    fake.close()


@pytest.mark.id("VERCEL-10")
def test_vercel10_set_email_dry_run_runs_nothing_and_asks_nothing(
    harness: Harness,
) -> None:
    run = harness.run(SCRIPT, "--dry-run")
    assert run.code == 0, run.text
    assert harness.calls() == []
    for line in (
        "[dry-run] $ vercel env add SMTP_HOST production --no-sensitive < smtp.gmail.com",
        "[dry-run] $ vercel env add SMTP_PORT preview --no-sensitive < 587",
        "[dry-run] $ vercel env add SMTP_PASSWORD production < <app password: 16 chars>",
        "[dry-run] $ vercel env add EMAIL_PROVIDER production --no-sensitive < smtp",
        "[dry-run] $ vercel env rm TEST_INBOX_SECRET production --yes",
        "[dry-run] $ vercel redeploy https://<current-production-deployment>.vercel.app",
        "[dry-run] POST https://kitchentab.vercel.app/api/v1/auth/otp/send",
    ):
        assert line in run.out, line
    assert "Gmail address:" not in run.out


@pytest.mark.id("VERCEL-10", "AUTH-15")
def test_vercel10_set_email_stores_gmail_redeploys_and_confirms_a_real_code(
    harness: Harness, prod: FakeProd
) -> None:
    run = harness.run(SCRIPT, stdin=f"{ADDRESS}\n{APP_PASSWORD}\n{CODE}\n")
    assert run.code == 0, run.text
    for env in ("production", "preview"):
        values = harness.env_vars(env)
        assert values["EMAIL_PROVIDER"] == "smtp"
        assert values["SMTP_HOST"] == "smtp.gmail.com" and values["SMTP_PORT"] == "587"
        assert values["SMTP_USER"] == ADDRESS
        assert values["SMTP_PASSWORD"] == "abcdefghijklmnop"
        assert values["EMAIL_FROM"] == f"KitchenTab <{ADDRESS}>"
        assert "TEST_INBOX_SECRET" not in values
    argvs = harness.vercel_argvs()
    assert ["env", "rm", "EMAIL_PROVIDER", "production", "--yes"] in argvs
    redeploys = [a for a in argvs if a[:1] == ["redeploy"]]
    assert len(redeploys) == 1
    assert harness.state()["deployments"][-1]["email_provider"] == "smtp"
    assert prod.sent == [ADDRESS]
    assert "Test inbox is off" in run.out
    assert (
        "Signed in with the emailed code (this also created your account). Email delivery works."
        in run.out
    )
    # The app password and the full address never reach the terminal or the call log.
    harness.assert_no_secrets(run, extra=("abcdefghijklmnop", APP_PASSWORD, ADDRESS))


@pytest.mark.id("VERCEL-10")
def test_vercel10_set_email_wrong_code_then_right_code(
    harness: Harness, prod: FakeProd
) -> None:
    run = harness.run(SCRIPT, stdin=f"{ADDRESS}\n{APP_PASSWORD}\n000000\n{CODE}\n")
    assert run.code == 0, run.text
    assert "That code didn't match. 4 tries left." in run.out
    run = harness.run(
        SCRIPT, stdin=f"{ADDRESS}\n{APP_PASSWORD}\n111111\n222222\n333333\n"
    )
    assert run.code == 1 and "The code was never confirmed." in run.err


@pytest.mark.id("VERCEL-10")
def test_vercel10_set_email_rejects_a_normal_password_before_touching_vercel(
    harness: Harness, prod: FakeProd
) -> None:
    run = harness.run(SCRIPT, stdin=f"{ADDRESS}\nMyGooglePassword1!\n")
    assert run.code == 1
    assert "That isn't a Gmail app password" in run.err
    assert not [a for a in harness.vercel_argvs() if a[:1] == ["env"]]
    assert "MyGooglePassword1!" not in run.text


@pytest.mark.id("VERCEL-10")
def test_vercel10_set_email_cli_failures_are_clear(
    harness: Harness, prod: FakeProd
) -> None:
    run = harness.run(SCRIPT, stdin=f"{ADDRESS}\n{APP_PASSWORD}\n", fail="whoami")
    assert run.code == 1 and "Run `vercel login`" in run.err
    run = harness.run(SCRIPT, stdin=f"{ADDRESS}\n{APP_PASSWORD}\n", fail="network@6")
    assert run.code == 1
    assert "Network problem" in run.err and "re-run `make set-email`" in run.err
    run = harness.run(SCRIPT, stdin=f"{ADDRESS}\n{APP_PASSWORD}\n", fail="deploy")
    assert run.code == 1 and "The redeploy failed" in run.err
    harness.assert_no_secrets(run, extra=("abcdefghijklmnop",))


# ---------------------------------------------------------------------------- local, real API + Mailpit


@pytest.fixture(scope="module")
def mailpit() -> Iterator[tuple[str, int, str]]:
    from testcontainers.core.container import DockerContainer

    container = DockerContainer("axllent/mailpit:v1.24").with_exposed_ports(1025, 8025)
    with container:
        host = container.get_container_host_ip()
        smtp_port = int(container.get_exposed_port(1025))
        web = f"http://{host}:{container.get_exposed_port(8025)}"
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(web + "/api/v1/messages", timeout=2).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield host, smtp_port, web


def _code_from_mailpit(web: str, address: str, timeout: float = 60) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with urllib.request.urlopen(
            f"{web}/api/v1/search?query=to:{address}", timeout=5
        ) as resp:
            messages = json.loads(resp.read()).get("messages", [])
        for message in messages:
            m = re.match(r"(\d{6}) is your KitchenTab code", message.get("Subject", ""))
            if m:
                return m.group(1)
        time.sleep(0.5)
    raise AssertionError("no code arrived in Mailpit")


@pytest.mark.id("VERCEL-10", "AUTH-15")
def test_vercel10_set_email_local_end_to_end_through_mailpit(
    harness: Harness, migrated_db: str, mailpit: tuple[str, int, str]
) -> None:
    """The real API with EMAIL_PROVIDER=smtp does STARTTLS + login (Gmail's path) against the relay,
    the code lands in Mailpit, and typing it in signs in. Nothing touches Vercel."""
    host, smtp_port, web = mailpit
    address = f"set-email-{secrets.token_hex(4)}@example.test"
    password = "qrstuvwxyzabcdef"
    env = harness.env(
        extra={
            "DATABASE_URL": migrated_db,
            "SESSION_SECRET": "local-session-secret-0123456789abcdef",
            "TOKEN_HASH_PEPPER": "local-pepper-0123456789abcdef012345",
            "KITCHENTAB_ROOT": str(ROOT),  # the real apps/api
        }
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            str(DEPLOY / SCRIPT),
            "--local",
            "--mailpit-smtp",
            f"{host}:{smtp_port}",
            "--mailpit-url",
            web,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=ROOT,
    )
    lines: queue.Queue[str] = queue.Queue()
    out: list[str] = []
    reader = threading.Thread(
        target=lambda: [lines.put(line) for line in proc.stdout], daemon=True
    )  # type: ignore[union-attr]
    reader.start()
    assert proc.stdin is not None
    proc.stdin.write(f"{address}\n{password}\n")
    proc.stdin.flush()
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            assert proc.poll() is None, "".join(out) + proc.stderr.read()  # type: ignore[union-attr]
            continue
        out.append(line)
        if line.startswith("Type the 6-digit code"):
            break
    else:
        proc.kill()
        raise AssertionError("never asked for the code:\n" + "".join(out))
    code = _code_from_mailpit(web, address)
    proc.stdin.write("000000\n" + code + "\n")  # one wrong try first, like a typo
    proc.stdin.flush()
    proc.stdin.close()
    rc = proc.wait(timeout=60)
    reader.join(timeout=10)
    while not lines.empty():
        out.append(lines.get())
    err = proc.stderr.read()  # type: ignore[union-attr]
    text = "".join(out) + err
    assert rc == 0, text
    assert "Test inbox is off" in text
    assert "That code didn't match." in text
    assert "Signed in with the emailed code" in text
    assert (
        "Local check passed: STARTTLS, login and delivery through Mailpit all work."
        in text
    )
    assert password not in text and address not in text


@pytest.mark.id("VERCEL-10")
def test_vercel10_brevo_relay_logs_in_with_its_own_login_and_keeps_the_key_secret() -> (
    None
):
    sys.path.insert(0, str(DEPLOY))
    from deploylib import Secret
    from set_email import PROVIDERS, smtp_values

    host, port = PROVIDERS["brevo"]
    values = {
        name: (value, shown)
        for name, value, shown in smtp_values(
            "me@example.com",
            Secret("xsmtpsib-key-1234567890"),
            host,
            port,
            "8a1b2c001@smtp-brevo.com",
        )
    }
    assert (
        values["SMTP_HOST"][0] == "smtp-relay.brevo.com"
        and values["SMTP_PORT"][0] == "587"
    )
    assert values["SMTP_USER"][0] == "8a1b2c001@smtp-brevo.com"  # login, not the sender
    assert values["EMAIL_FROM"][0] == "KitchenTab <me@example.com>"
    assert isinstance(
        values["SMTP_PASSWORD"][0], Secret
    )  # stored sensitive, never echoed
    assert "xsmtpsib" not in values["SMTP_PASSWORD"][1]
    assert (
        list(values)[-1] == "EMAIL_PROVIDER"
    )  # switched only after the credentials exist
