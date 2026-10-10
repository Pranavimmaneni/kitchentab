"""`make launch-check TARGET=local-vercel|prod`: walk the deployed product end to end (BUILD_ALL §6).

Runs against a base URL over HTTPS, as real clients would: through the edge (vercel.json routing,
4.5 MB body limit), with email-code sign-in read from the test inbox (`/api/test/inbox` +
`X-Test-Secret`, ADR-0016). Every user it creates is `<name>+lc<run>@launch-check.kitchentab.test`
(deploylib.TEST_EMAIL_DOMAIN), so `make prod-clean-test-data` removes all of it.

Steps, in order (each prints PASS/FAIL/SKIP and its time; a step whose prerequisite failed is
skipped; any FAIL exits 1):

    health · security headers · PWA (manifest, service worker, HTTPS) · sign-up · house ·
    join by link (2nd and 3rd user) · text-PDF bill through the real upload path · split · send ·
    photo bill through the OCR path · payment · settle · kitchen item · ask -> add to split ·
    push to a test subscription · 19 MB / 10-page PDF via client upload + 5 MB JSON -> 413 ·
    SSE stream across the 270 s rollover (no missed or duplicate events)

Targets:
    local-vercel  base URL, test-inbox secret, CA file and fake push URL come from
                  .cache/local-vercel/state.json (`make local-vercel-up`).
    prod          base URL from --base-url, LAUNCH_CHECK_URL or .vercel/kitchentab-deploy.json;
                  the secret from TEST_INBOX_SECRET, else `vercel env pull` (production). Push is
                  printed as a manual check. Never chosen by default.

    uv run --project apps/api python scripts/launch_check.py --target local-vercel [--sse-seconds 30]

Stdlib + httpx. Never prints secrets, codes, tokens or full email addresses.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import os
import re
import secrets
import ssl
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "deploy"))
from deploylib import TEST_EMAIL_DOMAIN, mask_email

FIXTURES = ROOT / "packages" / "fixtures" / "receipts"
LV_STATE = ROOT / ".cache" / "local-vercel" / "state.json"
DEPLOY_STATE = ROOT / ".vercel" / "kitchentab-deploy.json"
TARGETS = ("local-vercel", "prod")

BODY_LIMIT = 4_500_000  # Vercel Hobby request body limit
SSE_ROLLOVER_S = 270.0  # app/routes/events.py MAX_STREAM_SECONDS
MAX_DURATION_S = 300.0  # Vercel Hobby max function duration
DEFAULT_SSE_SECONDS = 285.0  # wait for the server's rollover at 270 s, with a margin
BIG_PDF_PAGES = 10
BIG_PDF_MIN_BYTES = 19 * 1024 * 1024  # "19 MB"; the upload limit is 20 MiB (FR-10)
UPLOAD_LIMIT = 20 * 1024 * 1024
PDF_FIXTURE = "r01"  # ShopRite text PDF
PHOTO_FIXTURE = "i01"  # ShopRite photo with its recorded browser-OCR lines
BLOB_API_URL = (
    "https://vercel.com/api/blob"  # @vercel/blob's default API URL (client uploads)
)
BLOB_API_VERSION = "12"  # @vercel/blob 2.8.1


class CheckError(Exception):
    """A check found something wrong; the message says what (never a secret)."""


# ---------------------------------------------------------------------------- pure helpers


def make_big_pdf(
    pages: int = BIG_PDF_PAGES, min_bytes: int = BIG_PDF_MIN_BYTES
) -> bytes:
    """A valid PDF of `pages` pages with no text, each page one full-page 8-bit gray image of
    incompressible deterministic noise (SHAKE-256 of the page number), so the file is at least
    `min_bytes` and the same bytes every run. Readers see a scanned document (needs OCR)."""
    width = 1024
    per_page = -(-min_bytes // pages)  # ceil
    height = -(-per_page // width)
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled below
    pages_obj = add(b"")
    kids: list[int] = []
    content = b"q 612 0 0 792 0 0 cm /Im0 Do Q\n"
    for n in range(pages):
        pixels = hashlib.shake_256(f"kitchentab-launch-check-page-{n}".encode()).digest(
            width * height
        )
        image = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Length %d >>\nstream\n"
            % (width, height, len(pixels))
            + pixels
            + b"\nendstream"
        )
        stream = add(
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream"
        )
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_obj, image, stream)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj
    objects[pages_obj - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        xref,
    )
    return bytes(out)


@dataclass
class SseEvent:
    event: str | None
    data: str
    id: str | None

    def json(self) -> Any:
        return json.loads(self.data) if self.data else None


class SseParser:
    """Incremental text/event-stream parser (WHATWG rules; comments and `retry:` are dropped)."""

    def __init__(self) -> None:
        self._buf = ""
        self._data: list[str] = []
        self._event: str | None = None
        self._id: str | None = None

    def feed(self, chunk: str) -> list[SseEvent]:
        self._buf += chunk.replace("\r\n", "\n").replace("\r", "\n")
        out: list[SseEvent] = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line == "":
                if self._data or self._event:
                    out.append(SseEvent(self._event, "\n".join(self._data), self._id))
                self._data, self._event, self._id = [], None, None
                continue
            if line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "data":
                self._data.append(value)
            elif name == "event":
                self._event = value
            elif name == "id":
                self._id = value
        return out


def sequence_problems(received: list[int], expected: list[int]) -> list[str]:
    """What's wrong with the seqs a client received versus the house's committed events."""
    problems = []
    dups = sorted({s for s in received if received.count(s) > 1})
    if dups:
        problems.append(f"duplicate events: {dups[:10]}")
    if any(b <= a for a, b in itertools.pairwise(received)) and not dups:
        problems.append("events out of order")
    missing = sorted(set(expected) - set(received))
    if missing:
        problems.append(f"missed events: {missing[:10]}")
    extra = sorted(set(received) - set(expected))
    if extra:
        problems.append(f"events the catch-up doesn't know: {extra[:10]}")
    if expected and expected != list(range(expected[0], expected[0] + len(expected))):
        problems.append("the house's event sequence has gaps")
    return problems


MANIFEST_EXPECT: dict[str, Any] = {
    "id": "/",
    "start_url": "/?source=pwa",
    "scope": "/",
    "name": "KitchenTab",
    "short_name": "KitchenTab",
    "display": "standalone",
    "display_override": ["standalone"],
    "orientation": "portrait",
    "background_color": "#ECEAE2",
    "theme_color": "#121210",
}
MANIFEST_SHORTCUTS = ("/bills/new", "/kitchen/fridge", "/kitchen/requests")


def manifest_problems(m: dict[str, Any]) -> list[str]:
    """PRD §20.1, field by field."""
    problems = []
    for key, want in MANIFEST_EXPECT.items():
        got = m.get(key)
        if isinstance(want, str) and isinstance(got, str) and key.endswith("color"):
            got = got.upper()
            want = want.upper()
        if got != want:
            problems.append(f"{key} is {got!r}, want {want!r}")
    icons = m.get("icons") or []

    def has_icon(size: str, purpose: str) -> bool:
        return any(
            size in str(i.get("sizes", "")).split()
            and purpose in str(i.get("purpose", "any")).split()
            and i.get("type", "image/png") == "image/png"
            for i in icons
        )

    for size, purpose in (
        ("192x192", "any"),
        ("512x512", "any"),
        ("512x512", "maskable"),
    ):
        if not has_icon(size, purpose):
            problems.append(f"no {size} {purpose} PNG icon")
    narrow = [s for s in m.get("screenshots") or [] if s.get("form_factor") == "narrow"]
    if len(narrow) < 2:
        problems.append(f"{len(narrow)} narrow screenshots, want 2 (Kitchen, Split)")
    urls = {str(s.get("url", "")) for s in m.get("shortcuts") or []}
    for url in MANIFEST_SHORTCUTS:
        if url not in urls:
            problems.append(f"no shortcut to {url}")
    return problems


API_CSP_DEFAULT = "default-src 'none'"


def header_problems(
    h: httpx.Headers | dict[str, str], *, kind: str, require_hsts: bool
) -> list[str]:
    """Security headers for a web page (`kind="page"`) or an API response (`kind="api"`)."""
    get = (
        (lambda k: h.get(k))
        if isinstance(h, httpx.Headers)
        else (lambda k: {x.lower(): v for x, v in h.items()}.get(k.lower()))
    )
    problems = []
    csp = get("content-security-policy") or ""
    if not csp:
        problems.append(f"{kind}: no Content-Security-Policy")
    if get("x-content-type-options") != "nosniff":
        problems.append(f"{kind}: X-Content-Type-Options isn't nosniff")
    if not get("referrer-policy"):
        problems.append(f"{kind}: no Referrer-Policy")
    framing = (
        "frame-ancestors 'none'" in csp
        or (get("x-frame-options") or "").upper() == "DENY"
    )
    if not framing:
        problems.append(
            f"{kind}: can be framed (no frame-ancestors 'none' / X-Frame-Options DENY)"
        )
    if require_hsts and "max-age=" not in (get("strict-transport-security") or ""):
        problems.append(f"{kind}: no Strict-Transport-Security")
    if kind == "api":
        if csp and API_CSP_DEFAULT not in csp:
            problems.append("api: CSP doesn't start from default-src 'none'")
        if "no-store" not in (get("cache-control") or ""):
            problems.append("api: Cache-Control isn't no-store")
    elif csp:
        script = next(
            (d for d in csp.split(";") if d.strip().startswith("script-src")), ""
        )
        default = next(
            (d for d in csp.split(";") if d.strip().startswith("default-src")), ""
        )
        if not (script or default):
            problems.append("page: CSP has no script-src or default-src")
        if (
            "'unsafe-inline'" in script
            and "'nonce-" not in script
            and "'strict-dynamic'" not in script
        ):
            problems.append("page: script-src allows 'unsafe-inline'")
        if "'unsafe-eval'" in script:
            problems.append("page: script-src allows 'unsafe-eval'")
    return problems


COOKIE_ATTRS = ("secure", "httponly", "samesite=lax")


def session_cookie_problems(set_cookie_headers: list[str]) -> list[str]:
    session = [c for c in set_cookie_headers if c.lower().startswith("kt_session=")]
    if not session:
        return ["sign-in set no kt_session cookie"]
    attrs = {a.strip().lower() for a in session[-1].split(";")[1:]}
    return [f"kt_session cookie lacks {a}" for a in COOKIE_ATTRS if a not in attrs]


def test_email(name: str, run_id: str) -> str:
    return f"{name}+lc{run_id}@{TEST_EMAIL_DOMAIN}"


# ---------------------------------------------------------------------------- target config


@dataclass
class Config:
    target: str
    base_url: str
    inbox_secret: str = field(repr=False)
    verify: ssl.SSLContext | bool = True
    ca_file: str | None = None
    push_url: str | None = None  # fake push service (local-vercel only)
    sw_path: str = "/sw.js"
    sse_seconds: float = DEFAULT_SSE_SECONDS


def resolve_config(
    args: argparse.Namespace, env: dict[str, str] | None = None
) -> Config:
    """Where to point and with what secret. Never falls back to production by itself."""
    env = dict(os.environ if env is None else env)
    if args.target not in TARGETS:
        raise CheckError(
            f"--target must be one of {', '.join(TARGETS)} (got {args.target!r})"
        )
    sse = float(
        args.sse_seconds
        if args.sse_seconds is not None
        else env.get("LAUNCH_CHECK_SSE_SECONDS", DEFAULT_SSE_SECONDS)
    )
    if args.target == "local-vercel":
        try:
            state = json.loads(
                Path(args.state_file or LV_STATE).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise CheckError(
                "no local-vercel rehearsal found: run `make local-vercel-up` first"
            ) from exc
        base = args.base_url or state["base_url"]
        ca = args.ca_file or state.get("ca_file")
        secret = state.get("secrets", {}).get("TEST_INBOX_SECRET", "")
        verify: ssl.SSLContext | bool = (
            ssl.create_default_context(cafile=ca) if ca else True
        )
        return Config(
            "local-vercel",
            base.rstrip("/"),
            secret,
            verify,
            ca,
            state.get("push_url"),
            state.get("sw_path", "/sw.js"),
            sse,
        )
    base = args.base_url or env.get("LAUNCH_CHECK_URL")
    if not base:
        try:
            base = json.loads(DEPLOY_STATE.read_text(encoding="utf-8")).get(
                "production_url"
            )
        except (OSError, ValueError):
            base = None
    if not base:
        raise CheckError(
            "TARGET=prod needs the production URL: --base-url, LAUNCH_CHECK_URL or `make deploy` first"
        )
    if not base.startswith("https://") or "localhost" in base:
        raise CheckError(f"TARGET=prod must be an https production URL (got {base})")
    secret = env.get("TEST_INBOX_SECRET") or ""
    if not secret and not args.no_pull:
        secret = pull_prod_secret()
    if not secret:
        raise CheckError(
            "TARGET=prod needs TEST_INBOX_SECRET (or let launch-check pull it with the Vercel CLI)"
        )
    verify = ssl.create_default_context(cafile=args.ca_file) if args.ca_file else True
    return Config(
        "prod", base.rstrip("/"), secret, verify, args.ca_file, None, "/sw.js", sse
    )


def pull_prod_secret() -> (
    str
):  # pragma: no cover - talks to Vercel; only by hand in the morning
    from deploylib import Console, Runner, Vercel

    console = Console(dry_run=False)
    values = Vercel(Runner(console, ROOT, "launch-check TARGET=prod")).env_pull(
        "production"
    )
    return values.get("TEST_INBOX_SECRET", "")


# ---------------------------------------------------------------------------- HTTP clients


class User:
    """One signed-in person: a cookie jar, the CSRF double-submit, keys on every POST."""

    def __init__(self, cfg: Config, name: str, run_id: str) -> None:
        self.cfg = cfg
        self.name = name
        self.email = test_email(name, run_id)
        self.http = httpx.AsyncClient(
            base_url=cfg.base_url,
            verify=cfg.verify,
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={
                "user-agent": "KitchenTab launch-check/1.0 (Windows NT 10.0) Chrome/140"
            },
        )
        self.user_id: str | None = None
        self.member_id: str | None = None

    async def aclose(self) -> None:
        await self.http.aclose()

    def csrf(self) -> str:
        return self.http.cookies.get("kt_csrf") or ""

    async def ensure_csrf(self) -> None:
        if not self.csrf():
            r = await self.http.get("/api/v1/auth/csrf")
            expect(r, 200, "GET /auth/csrf")

    async def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        want: int | tuple[int, ...] = (200, 201),
        if_match: int | None = None,
        idem: bool | None = None,
    ) -> Any:
        headers = {"X-Client-Mutation-Id": str(uuid.uuid4())}
        if method != "GET":
            await self.ensure_csrf()
            headers["X-CSRF-Token"] = self.csrf()
        if idem if idem is not None else method == "POST":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        r = await self.http.request(
            method, f"/api/v1{path}", json=body, headers=headers
        )
        expect(r, want, f"{method} {path}")
        return (
            r.json()
            if r.content and "json" in r.headers.get("content-type", "")
            else None
        )

    async def sign_in(self) -> list[str]:
        """Email code through the test inbox; returns the verify response's Set-Cookie headers."""
        await self.ensure_csrf()
        await self.call(
            "POST",
            "/auth/otp/send",
            {"channel": "email", "destination": self.email},
            idem=False,
        )
        code = await read_code(self.http, self.cfg.inbox_secret, self.email)
        await self.ensure_csrf()
        headers = {"X-CSRF-Token": self.csrf()}
        r = await self.http.post(
            "/api/v1/auth/otp/verify",
            json={"channel": "email", "destination": self.email, "code": code},
            headers=headers,
        )
        expect(r, 200, "POST /auth/otp/verify")
        self.user_id = r.json().get("user_id")
        return r.headers.get_list("set-cookie")


def expect(r: httpx.Response, want: int | tuple[int, ...], what: str) -> None:
    wanted = (want,) if isinstance(want, int) else want
    if r.status_code not in wanted:
        detail = ""
        if "json" in r.headers.get("content-type", ""):
            try:
                body = r.json()
                detail = f": {body.get('title') or body.get('detail') or ''} {body.get('code', '')}".rstrip()
            except ValueError:
                detail = ""
        raise CheckError(f"{what} answered {r.status_code}{detail}")


async def read_code(
    http: httpx.AsyncClient, secret: str, email: str, timeout: float = 20.0
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await http.get(
            "/api/test/inbox",
            params={"to": email, "limit": 1},
            headers={"X-Test-Secret": secret},
        )
        if r.status_code == 404:
            raise CheckError(
                "the test inbox is off (/api/test/inbox 404): EMAIL_PROVIDER isn't testinbox"
            )
        expect(r, 200, "GET /api/test/inbox")
        for message in r.json():
            m = re.search(r"\b(\d{6})\b", message.get("subject", ""))
            if m:
                return m.group(1)
        await asyncio.sleep(0.5)
    raise CheckError(
        f"no sign-in code reached the test inbox for {mask_email(email)} in {timeout:.0f} s"
    )


# ---------------------------------------------------------------------------- the run


@dataclass
class Step:
    name: str
    fn: Callable[[Run], Awaitable[str | None]]
    needs: tuple[str, ...] = ()


@dataclass
class Result:
    name: str
    status: str  # PASS FAIL SKIP MANUAL
    ms: float
    detail: str = ""


class Run:
    def __init__(self, cfg: Config, out: Callable[[str], None] = print) -> None:
        self.cfg = cfg
        self.out = out
        self.run_id = secrets.token_hex(4)
        self.users: dict[str, User] = {}
        self.house_id = ""
        self.invite_token = ""
        self.members: dict[str, str] = {}  # name -> member id
        self.bill1 = ""
        self.bill2 = ""
        self.link_token = ""
        self.leftover_id = ""
        self.push_user = ""  # fake push service user tag (local-vercel)
        self.results: list[Result] = []

    def user(self, name: str) -> User:
        if name not in self.users:
            self.users[name] = User(self.cfg, name, self.run_id)
        return self.users[name]

    @property
    def a(self) -> User:
        return self.user("pranav")

    @property
    def b(self) -> User:
        return self.user("arjun")

    @property
    def c(self) -> User:
        return self.user("meera")

    def anon(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.cfg.base_url,
            verify=self.cfg.verify,
            timeout=httpx.Timeout(60.0, connect=15.0),
            **kw,
        )

    async def execute(self, steps: list[Step]) -> int:
        self.out(
            f"launch-check TARGET={self.cfg.target} {self.cfg.base_url} (run {self.run_id})"
        )
        status: dict[str, str] = {}
        width = max(len(s.name) for s in steps) + 2
        try:
            for i, step in enumerate(steps, start=1):
                blocked = [
                    n for n in step.needs if status.get(n) not in ("PASS", "MANUAL")
                ]
                started = time.perf_counter()
                if blocked:
                    res = Result(step.name, "SKIP", 0.0, f"needs {', '.join(blocked)}")
                else:
                    try:
                        note = await step.fn(self)
                        res = Result(
                            step.name,
                            "MANUAL" if note and note.startswith("MANUAL") else "PASS",
                            (time.perf_counter() - started) * 1000,
                            note or "",
                        )
                    except (CheckError, AssertionError, httpx.HTTPError) as exc:
                        msg = str(exc) or type(exc).__name__
                        if isinstance(exc, httpx.HTTPError):
                            msg = f"{type(exc).__name__}: {exc}"
                        res = Result(
                            step.name,
                            "FAIL",
                            (time.perf_counter() - started) * 1000,
                            msg,
                        )
                status[step.name] = res.status
                self.results.append(res)
                label = f"{i:2} {step.name} ".ljust(width + 4, ".")
                self.out(f"  {res.status:6} {label} {fmt_ms(res.ms)}")
                for line in res.detail.splitlines():
                    self.out(f"         {line}")
        finally:
            for u in self.users.values():
                await u.aclose()
        counts = {
            s: sum(1 for r in self.results if r.status == s)
            for s in ("PASS", "FAIL", "SKIP", "MANUAL")
        }
        total = sum(r.ms for r in self.results)
        self.out(
            f"launch-check: {counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped"
            + (f", {counts['MANUAL']} to check by hand" if counts["MANUAL"] else "")
            + f" in {total / 1000:.1f} s"
        )
        return 1 if counts["FAIL"] or counts["SKIP"] else 0


def fmt_ms(ms: float) -> str:
    return f"{ms / 1000:7.1f} s" if ms >= 10_000 else f"{ms:7.0f} ms"


# ---------------------------------------------------------------------------- steps


async def step_health(run: Run) -> str:
    async with run.anon() as http:
        r = await http.get("/api/v1/health")
        expect(r, 200, "GET /api/v1/health")
        if r.json().get("status") != "ok":
            raise CheckError(f"health says {r.json().get('status')!r}")
        ready = await http.get("/api/v1/health/ready")
        expect(ready, 200, "GET /api/v1/health/ready")
        if not ready.json().get("database"):
            raise CheckError("the API can't reach the database")
        # vercel.json routing: /api/(.*) -> FastAPI (JSON problems), everything else -> Next.js
        api_404 = await http.get("/api/v1/launch-check-no-such-route")
        if "application/problem+json" not in api_404.headers.get("content-type", ""):
            raise CheckError("/api/v1/* isn't answered by the API service")
        page = await http.get("/manifest.webmanifest")
        if page.status_code != 200 or "json" not in page.headers.get(
            "content-type", ""
        ):
            raise CheckError("/manifest.webmanifest isn't served by the web service")
        blob_route = await http.get("/api/blob-upload")
        if "application/problem+json" in blob_route.headers.get("content-type", ""):
            raise CheckError(
                "/api/blob-upload reached the API; vercel.json must send it to the web service"
            )
    return f"env={r.json().get('env')}; routing: /api/* -> api, /api/blob-upload + /(.*) -> web"


async def step_headers(run: Run) -> str:
    prod = run.cfg.target == "prod"
    async with run.anon() as http:
        page = await http.get("/start")
        expect(page, 200, "GET /start")
        api = await http.get("/api/v1/health")
        problems = header_problems(page.headers, kind="page", require_hsts=prod)
        problems += header_problems(api.headers, kind="api", require_hsts=prod)
        if prod:
            docs = await http.get("/api/v1/docs")
            if docs.status_code != 404:
                problems.append("api: /api/v1/docs is public in production")
    if problems:
        raise CheckError("\n".join(problems))
    return "" if prod else "HSTS not required here (APP_ENV=test; ADR-0028)"


THEME_META = (("light", "#ECEAE2"), ("dark", "#0D0D0B"))


async def step_pwa(run: Run) -> str:
    problems: list[str] = []
    if urlsplit(run.cfg.base_url).scheme != "https":
        problems.append("the base URL isn't https")
    async with run.anon() as http:
        r = await http.get("/manifest.webmanifest")
        expect(r, 200, "GET /manifest.webmanifest")
        if (
            r.headers.get("content-type", "").split(";")[0]
            != "application/manifest+json"
        ):
            problems.append(f"manifest content-type is {r.headers.get('content-type')}")
        manifest = r.json()
        problems += manifest_problems(manifest)
        for icon in manifest.get("icons") or []:
            got = await http.get(str(icon.get("src")))
            if got.status_code != 200 or not got.headers.get(
                "content-type", ""
            ).startswith("image/"):
                problems.append(f"icon {icon.get('src')} answered {got.status_code}")
        page = (await http.get("/start")).text
        for scheme, color in THEME_META:
            pattern = rf'<meta[^>]+name="theme-color"[^>]+content="{color}"[^>]+prefers-color-scheme: {scheme}'
            alt = rf'<meta[^>]+media="\(prefers-color-scheme: {scheme}\)"[^>]+content="{color}"'
            if not (
                re.search(pattern, page, re.IGNORECASE)
                or re.search(alt, page, re.IGNORECASE)
            ):
                problems.append(f"no {scheme} theme-color meta {color}")
        if not re.search(r'<link[^>]+rel="apple-touch-icon"', page):
            problems.append("no apple-touch-icon link")
        if not re.search(r'<link[^>]+rel="manifest"', page):
            problems.append("no manifest link")
        sw = await http.get(run.cfg.sw_path)
        if sw.status_code != 200 or "javascript" not in sw.headers.get(
            "content-type", ""
        ):
            problems.append(
                f"service worker {run.cfg.sw_path} answered {sw.status_code} {sw.headers.get('content-type', '')}"
            )
        if run.cfg.target == "prod":
            plain = await http.get(
                "http://" + run.cfg.base_url.removeprefix("https://") + "/start"
            )
            if plain.status_code not in (301, 308) or not plain.headers.get(
                "location", ""
            ).startswith("https://"):
                problems.append("http:// doesn't redirect to https://")
    if problems:
        raise CheckError("\n".join(problems))
    return "TLS verified" + (
        f" against {Path(run.cfg.ca_file).name}" if run.cfg.ca_file else ""
    )


async def step_signup(run: Run) -> str:
    cookies = await run.a.sign_in()
    problems = session_cookie_problems(cookies)
    if problems:
        raise CheckError("\n".join(problems))
    me = await run.a.call("GET", "/me")
    if not me:
        raise CheckError("GET /me returned nothing after sign-in")
    methods = await run.a.call("GET", "/auth/methods")
    return f"{mask_email(run.a.email)} signed in; cookie Secure+HttpOnly+SameSite=Lax; methods {methods}"


async def step_house(run: Run) -> str:
    created = await run.a.call(
        "POST",
        "/houses",
        {
            "name": "Launch check",
            "roommates": ["Arjun", "Meera"],
            "timezone": "America/New_York",
        },
    )
    run.house_id = created["house"]["id"]
    for m in created["members"]:
        run.members[m["display_name"].lower()] = m["id"]
    me = next(
        m
        for m in created["members"]
        if m["display_name"].lower() not in ("arjun", "meera")
    )
    run.members["pranav"] = me["id"]
    run.a.member_id = me["id"]
    view = await run.a.call("GET", f"/houses/{run.house_id}")
    run.invite_token = view["invite_token"]
    return f"house with {len(created['members'])} members"


async def step_join(run: Run) -> str:
    async with run.anon() as http:
        page = await http.get(f"/h/{run.invite_token}")
        expect(page, 200, "GET /h/<invite> (web)")
        public = await http.get(f"/api/v1/join/{run.invite_token}")
        expect(public, 200, "GET /join/<invite>")
    for who in (run.b, run.c):
        await who.sign_in()
        if run.cfg.target == "local-vercel" and who is run.b:
            await register_push(run, who)
        await who.call(
            "POST",
            f"/join/{run.invite_token}/claim",
            {"member_id": run.members[who.name]},
        )
        who.member_id = run.members[who.name]
    view = await run.a.call("GET", f"/houses/{run.house_id}")
    claimed = sorted(m["display_name"] for m in view["members"] if m.get("claimed"))
    if len(claimed) != 3:
        raise CheckError(f"claimed members: {claimed}")
    return "Arjun and Meera joined by the invite link"


async def upload_file(
    run: Run, who: User, bill_id: str, data: bytes, mime: str, pages: int | None = None
) -> dict[str, Any]:
    """What the browser does (ADR-0015): ticket -> file straight to storage -> attach."""
    ticket = await who.call(
        "POST",
        "/uploads/authorize",
        {"bill_id": bill_id, "mime": mime, "bytes": len(data), "pages": pages},
    )
    key = ticket["storage_key"]
    if (
        ticket["method"] == "PUT"
    ):  # fake Blob (local-vercel) / MinIO: the API minted the token
        if urlsplit(ticket["upload_url"]).netloc == urlsplit(run.cfg.base_url).netloc:
            raise CheckError(
                "the upload URL points at the app origin; files must bypass the functions"
            )
        async with httpx.AsyncClient(
            verify=run.cfg.verify, timeout=httpx.Timeout(300.0, connect=15.0)
        ) as http:
            put = await http.put(
                ticket["upload_url"], content=data, headers=ticket["headers"]
            )
        expect(put, (200, 201), "PUT file to storage")
    elif (
        ticket["method"] == "blob-client"
    ):  # Vercel Blob: @vercel/blob/client upload() protocol
        await blob_client_upload(run, who, ticket, data, mime, bill_id, pages)
    else:
        raise CheckError(f"unknown upload method {ticket['method']!r}")
    attached = await who.call(
        "POST",
        f"/bills/{bill_id}/receipts",
        {"storage_key": key, "mime": mime, "bytes": len(data)},
    )
    return attached


async def blob_client_upload(
    run: Run,
    who: User,
    ticket: dict[str, Any],
    data: bytes,
    mime: str,
    bill_id: str,
    pages: int | None,
) -> (
    None
):  # pragma: no cover - production only (Vercel Blob); unverified until the morning
    blob = ticket.get("blob") or {}
    route = blob.get("handle_upload_url") or "/api/blob-upload"
    payload = {
        "type": "blob.generate-client-token",
        "payload": {
            "pathname": blob.get("pathname") or ticket["storage_key"],
            "clientPayload": json.dumps(
                {"bill_id": bill_id, "mime": mime, "bytes": len(data), "pages": pages}
            ),
            "multipart": False,
        },
    }
    await who.ensure_csrf()
    r = await who.http.post(route, json=payload, headers={"X-CSRF-Token": who.csrf()})
    expect(r, 200, f"POST {route}")
    token = r.json().get("clientToken")
    if not token:
        raise CheckError(f"{route} returned no clientToken")
    url = f"{BLOB_API_URL}/?pathname={quote(payload['payload']['pathname'], safe='/')}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as http:
        put = await http.put(
            url,
            content=data,
            headers={
                "authorization": f"Bearer {token}",
                "x-api-version": BLOB_API_VERSION,
                "x-content-type": mime,
                "x-content-length": str(len(data)),
            },
        )
    expect(put, 200, "PUT file to Vercel Blob")


def truth(fid: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / f"{fid}.truth.json").read_text(encoding="utf-8")
    )
    return data


async def step_pdf_bill(run: Run) -> str:
    t = truth(PDF_FIXTURE)
    bill = await run.a.call("POST", f"/houses/{run.house_id}/bills", {})
    run.bill1 = bill["id"]
    data = (FIXTURES / f"{PDF_FIXTURE}.pdf").read_bytes()
    attached = await upload_file(
        run, run.a, run.bill1, data, "application/pdf", pages=1
    )
    if attached.get("extraction") != "done":
        raise CheckError(
            f"text PDF extraction is {attached.get('extraction')!r}, want 'done'"
        )
    view = await run.a.call("GET", f"/bills/{run.bill1}")
    items = view["items"]
    problems = []
    if len(items) != len(t["items"]):
        problems.append(f"{len(items)} items, receipt has {len(t['items'])}")
    if view["bill"]["total_cents"] != t["total_cents"]:
        problems.append(
            f"total {view['bill']['total_cents']} cents, receipt says {t['total_cents']}"
        )
    if view["bill"]["receipt_total_cents"] != t["total_cents"]:
        problems.append(
            f"printed total {view['bill']['receipt_total_cents']}, want {t['total_cents']}"
        )
    if not view["reconciliation"].get("ok"):
        problems.append(f"review shows a mismatch: {view['reconciliation']}")
    if problems:
        raise CheckError("\n".join(problems))
    receipts = await run.a.call("GET", f"/bills/{run.bill1}/receipts")
    shown = await run.a.call("GET", f"/receipts/{receipts[0]['id']}/view")
    if not shown.get("url"):
        raise CheckError("no view URL for the stored receipt")
    return f"{len(items)} items, total ${t['total_cents'] / 100:.2f} ({t['store_name']}), review reconciles"


async def step_split(run: Run) -> str:
    view = await run.a.call("GET", f"/bills/{run.bill1}")
    keys = {n: f"m:{run.members[n]}" for n in ("pranav", "arjun", "meera")}
    changes = []
    for item in view["items"]:
        who = (
            ("pranav", "arjun")
            if item["storage"] == "fridge"
            else ("pranav", "arjun", "meera")
        )
        changes += [
            {"item_id": item["id"], "participant": keys[n], "on": True} for n in who
        ]
    # In a draft only the payer ticks (policy._decide_bill); roommates tick their own once it's sent.
    await run.a.call("PUT", f"/bills/{run.bill1}/allocations", {"changes": changes})
    after = await run.a.call("GET", f"/bills/{run.bill1}")
    if after["progress"]["assigned"] != after["progress"]["total"]:
        raise CheckError(f"{after['progress']} items assigned")
    shares = after["shares"]
    total = after["bill"]["total_cents"]
    if sum(shares.values()) != total:
        raise CheckError(f"shares add up to {sum(shares.values())}, total is {total}")
    return "payer split it; " + ", ".join(
        f"{n} ${shares[k] / 100:.2f}" for n, k in keys.items()
    )


async def step_send(run: Run) -> str:
    sent = await run.a.call("POST", f"/bills/{run.bill1}/send")
    run.link_token = sent.get("link_token", "")
    view = await run.c.call("GET", f"/bills/{run.bill1}")
    if view["bill"]["state"] != "open":
        raise CheckError(f"bill state is {view['bill']['state']!r}")
    # On the open tab a roommate ticks herself onto a line (FR-32): shares move, still sum to the total.
    me = f"m:{run.members['meera']}"
    line = next(
        i
        for i in view["items"]
        if all(a["participant"] != me for a in i["allocations"])
    )
    await run.c.call(
        "PUT",
        f"/bills/{run.bill1}/allocations",
        {"changes": [{"item_id": line["id"], "participant": me, "on": True}]},
    )
    after = await run.a.call("GET", f"/bills/{run.bill1}")
    if sum(after["shares"].values()) != after["bill"]["total_cents"]:
        raise CheckError("shares no longer add up to the total after Meera's tick")
    async with run.anon() as http:
        page = await http.get(f"/b/{run.link_token}")
        expect(page, 200, "GET /b/<link> (web)")
        if page.headers.get("referrer-policy") != "no-referrer":
            raise CheckError(
                "the bill link page doesn't send Referrer-Policy: no-referrer"
            )
    return (
        f"bill open; Meera ticked {line['name']} herself; link page renders signed out"
    )


async def step_photo_bill(run: Run) -> str:
    t = truth(PHOTO_FIXTURE)
    payload = json.loads(
        (FIXTURES / f"{PHOTO_FIXTURE}.ocr.json").read_text(encoding="utf-8")
    )
    bill = await run.a.call("POST", f"/houses/{run.house_id}/bills", {})
    run.bill2 = bill["id"]
    photo = (FIXTURES / f"{PHOTO_FIXTURE}.jpg").read_bytes()
    attached = await upload_file(run, run.a, run.bill2, photo, "image/jpeg")
    if attached.get("extraction") != "needs_ocr":
        raise CheckError(
            f"photo extraction is {attached.get('extraction')!r}, want 'needs_ocr'"
        )
    receipt_id = attached["receipt"]["id"]
    ocr = await run.a.call(
        "POST",
        f"/bills/{run.bill2}/ocr",
        {"receipt_id": receipt_id, "parts": [{"pages": payload["pages"]}]},
    )
    if ocr.get("extraction") != "done":
        raise CheckError(f"OCR parse is {ocr.get('extraction')!r}")
    view = await run.a.call("GET", f"/bills/{run.bill2}")
    if view["bill"]["receipt_total_cents"] != t["total_cents"]:
        raise CheckError(
            f"printed total {view['bill']['receipt_total_cents']}, receipt says {t['total_cents']}"
        )
    if not view["items"]:
        raise CheckError("no items from the OCR lines")
    if not view["reconciliation"].get("ok"):
        raise CheckError(f"OCR bill doesn't reconcile: {view['reconciliation']}")
    keys = [f"m:{run.members['pranav']}", f"m:{run.members['arjun']}"]
    changes = [
        {"item_id": i["id"], "participant": k, "on": True}
        for i in view["items"]
        for k in keys
    ]
    await run.a.call("PUT", f"/bills/{run.bill2}/allocations", {"changes": changes})
    await run.a.call("POST", f"/bills/{run.bill2}/send")
    return f"{len(view['items'])} items from {sum(len(p['lines']) for p in payload['pages'])} OCR lines; total ${t['total_cents'] / 100:.2f}; split and sent"


async def step_payment(run: Run) -> str:
    paid = await run.b.call("POST", f"/bills/{run.bill1}/payments", {"method": "venmo"})
    confirmed = await run.a.call("POST", f"/payments/{paid['id']}/confirm")
    if confirmed.get("status") not in (
        "confirmed",
        None,
    ) and "confirm" not in json.dumps(confirmed):
        raise CheckError(f"payment status {confirmed.get('status')!r}")
    return f"Arjun paid ${paid.get('amount_cents', 0) / 100:.2f} by Venmo; Pranav confirmed"


async def step_settle(run: Run) -> str:
    out = await run.a.call(
        "POST", f"/bills/{run.bill1}/settle", {"confirm_outstanding": True}
    )
    view = await run.a.call("GET", f"/bills/{run.bill1}")
    if view["bill"]["state"] != "settled":
        raise CheckError(f"bill state is {view['bill']['state']!r} after settle")
    balances = await run.c.call("GET", f"/houses/{run.house_id}/balances")
    return f"settled ({'carryover kept' if out else 'no carryover'}); balances readable: {bool(balances)}"


async def step_kitchen_item(run: Run) -> str:
    made = await run.b.call(
        "POST",
        f"/houses/{run.house_id}/inventory",
        {"name": "Dal leftovers", "place": "fridge"},
    )
    run.leftover_id = made["id"]
    inv = await run.c.call("GET", f"/houses/{run.house_id}/inventory", None)
    names = [i["name"] for a in inv.get("areas", []) for i in a.get("items", [])]
    if "Dal leftovers" not in names:
        raise CheckError("Meera can't see Arjun's leftovers in the fridge")
    return f"fridge shows {len(names)} items to Meera"


async def step_ask_split(run: Run) -> str:
    inv = await run.c.call("GET", f"/houses/{run.house_id}/inventory")
    item = next(
        (
            i
            for a in inv.get("areas", [])
            for i in a.get("items", [])
            if i.get("bill_id") == run.bill2
            or (i.get("source") or {}).get("bill_id") == run.bill2
        ),
        None,
    )
    if item is None:  # fall back to any item Meera doesn't own that came from a bill
        item = next(
            (
                i
                for a in inv.get("areas", [])
                for i in a.get("items", [])
                if not i.get("mine") and i["name"] != "Dal leftovers"
            ),
            None,
        )
    if item is None:
        raise CheckError("no bought item in the kitchen for Meera to ask about")
    ask = await run.c.call(
        "POST",
        f"/inventory/{item['id']}/requests",
        {"amount": "half", "note": "For dinner"},
    )
    answer = await run.a.call(
        "POST", f"/requests/{ask['id']}/answer", {"decision": "split"}
    )
    if answer.get("resolution") != "split":
        raise CheckError(f"answer resolution {answer.get('resolution')!r}")
    view = await run.c.call("GET", f"/bills/{run.bill2}")
    me = f"m:{run.members['meera']}"
    share = view["shares"].get(me, 0)
    if share <= 0:
        raise CheckError("Meera has no share on the bill after 'add to split'")
    return f"Meera asked for {item['name']}; Pranav added her to the split (+${share / 100:.2f})"


async def register_push(run: Run, who: User) -> None:
    """A test subscription at the fake push service (local-vercel), quiet hours off for this user."""
    assert run.cfg.push_url
    run.push_user = f"lc-{run.run_id}"
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.post(
            f"{run.cfg.push_url}/subscriptions", json={"user": run.push_user}
        )
        expect(r, 200, "fake push POST /subscriptions")
        sub = r.json()
    await who.call("PUT", "/me/notification-prefs", {"quiet_hours": False})
    await who.call(
        "POST",
        "/push/subscriptions",
        {
            "endpoint": sub["endpoint"],
            "keys": sub["keys"],
            "device_label": "launch-check",
        },
        idem=False,
        want=201,
    )


async def step_push(run: Run) -> str:
    if run.cfg.target != "local-vercel":
        return (
            "MANUAL: on your phone (signed in, notifications on): Settings > Notifications > Send a test\n"
            "should buzz within seconds; then have a roommate send a bill that includes you."
        )
    await run.b.call("POST", "/push/test", idem=False, want=202)
    deadline = time.monotonic() + 30
    titles: list[str] = []
    ids: list[str] = []
    keys: set[str] = set()
    async with httpx.AsyncClient(timeout=15.0) as http:
        while time.monotonic() < deadline:
            got = (
                await http.get(
                    f"{run.cfg.push_url}/__test/push", params={"user": run.push_user}
                )
            ).json()
            payloads = [p.get("payload") or {} for p in got]
            titles = [str(p.get("title", "")) for p in payloads]
            ids = [
                str(p.get("event_id") or p.get("tag") or p.get("title"))
                for p in payloads
            ]
            keys = {str(p.get("vapid_public_key")) for p in got}
            if "It works!" in titles and any("split a" in t for t in titles):
                break
            await asyncio.sleep(0.5)
    problems = []
    if "It works!" not in titles:
        problems.append("POST /push/test never reached the test subscription")
    if not any("split a" in t for t in titles):
        problems.append("Arjun's 'new tab includes you' push never arrived")
    server_key = (await run.b.call("GET", "/push/config"))["vapid_public_key"]
    if titles and keys != {server_key}:
        problems.append(
            "pushes were signed with a VAPID key other than the one /push/config publishes"
        )
    if len(ids) != len(set(ids)):
        problems.append(
            f"the same event was pushed twice: {sorted(i for i in ids if ids.count(i) > 1)}"
        )
    if problems:
        raise CheckError("\n".join(problems) + f"\n(received: {titles})")
    return f"via queue + subscriber + Web Push (VAPID, RFC 8291): {titles}"


async def step_big_upload(run: Run) -> str:
    data = make_big_pdf()
    if not (BIG_PDF_MIN_BYTES <= len(data) <= UPLOAD_LIMIT):
        raise CheckError(f"generated PDF is {len(data)} bytes")
    bill = await run.a.call("POST", f"/houses/{run.house_id}/bills", {})
    started = time.perf_counter()
    attached = await upload_file(
        run, run.a, bill["id"], data, "application/pdf", pages=BIG_PDF_PAGES
    )
    took = time.perf_counter() - started
    if attached["receipt"]["bytes"] != len(data):
        raise CheckError(
            f"stored {attached['receipt']['bytes']} bytes, sent {len(data)}"
        )
    # The same size through a function is refused at the edge.
    big_json = {"store_name": "x", "padding": "x" * (5 * 1000 * 1000)}
    await run.a.ensure_csrf()
    r = await run.a.http.post(
        f"/api/v1/houses/{run.house_id}/bills",
        json=big_json,
        headers={"X-CSRF-Token": run.a.csrf(), "Idempotency-Key": str(uuid.uuid4())},
    )
    if r.status_code != 413:
        raise CheckError(f"a 5 MB JSON body answered {r.status_code}, want 413")
    by = (
        "the API" if "problem+json" in r.headers.get("content-type", "") else "the edge"
    )
    return (
        f"{len(data) / 1_000_000:.1f} MB, {BIG_PDF_PAGES} pages via client upload in {took:.1f} s "
        f"(extraction {attached.get('extraction')}); 5 MB JSON -> 413 from {by}"
    )


async def step_sse(run: Run) -> str:
    """Hold the house stream across the server's 270 s rollover while another member writes;
    reconnect with Last-Event-ID; nothing missed, nothing twice."""
    wait = run.cfg.sse_seconds
    full = wait >= SSE_ROLLOVER_S
    writer = run.b
    toggle = {"open": False}
    write_count = 0

    async def write() -> None:
        nonlocal write_count
        toggle["open"] = not toggle["open"]
        await writer.call(
            "PUT", f"/inventory/{run.leftover_id}/sharing", {"open": toggle["open"]}
        )
        write_count += 1

    received: list[int] = []
    path = f"/api/v1/houses/{run.house_id}/events"
    reader = run.a.http
    interval = 15.0 if full else max(wait / 4, 2.0)
    start_seq = 0
    rollover_at: float | None = None
    opened = time.monotonic()

    async def consume(
        last: int | None, until: float, stop_on_rollover: bool
    ) -> tuple[int, bool]:
        """Read one stream until `until` (monotonic) or its end; returns (last seq, rolled over)."""
        nonlocal start_seq, rollover_at
        headers = {"Accept": "text/event-stream"}
        if last is not None:
            headers["Last-Event-ID"] = str(last)
        parser = SseParser()
        rolled = False
        timeout = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
        async with reader.stream("GET", path, headers=headers, timeout=timeout) as resp:
            expect(resp, 200, "GET events (SSE)")
            it = resp.aiter_text()
            while True:
                remaining = until - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(it.__anext__(), timeout=remaining)
                except (StopAsyncIteration, TimeoutError):
                    break
                for ev in parser.feed(chunk):
                    if ev.event == "ready" and last is None:
                        start_seq = int(ev.json()["seq"])
                        last = start_seq
                    elif ev.event == "rollover":
                        rolled = True
                        rollover_at = time.monotonic() - opened
                    elif ev.event in ("resync", "revoked"):
                        raise CheckError(f"stream sent {ev.event}")
                    elif ev.event is None and ev.id:
                        received.append(int(ev.id))
                        last = int(ev.id)
                if rolled and stop_on_rollover:
                    break
        return (last if last is not None else start_seq), rolled

    async def writes_until(deadline: float) -> None:
        while time.monotonic() + interval < deadline:
            await asyncio.sleep(interval)
            await write()

    first_deadline = opened + (MAX_DURATION_S if full else wait)
    reader_task = asyncio.create_task(consume(None, first_deadline, True))
    writer_task = asyncio.create_task(
        writes_until(opened + min(wait, SSE_ROLLOVER_S - 5))
    )
    last, rolled = await reader_task
    writer_task.cancel()
    try:
        await writer_task
    except asyncio.CancelledError:
        pass
    held = time.monotonic() - opened
    problems = []
    if full:
        if not rolled:
            problems.append(
                f"no rollover event within {MAX_DURATION_S:.0f} s (stream ended after {held:.0f} s)"
            )
        elif rollover_at is not None and not (
            SSE_ROLLOVER_S - 5 <= rollover_at <= SSE_ROLLOVER_S + 15
        ):
            problems.append(
                f"rollover after {rollover_at:.0f} s, want about {SSE_ROLLOVER_S:.0f} s"
            )
    # Changes made while no stream is open must arrive on the reconnect (Last-Event-ID replay).
    await write()
    await write()
    tail_deadline = time.monotonic() + 20
    reconnect = asyncio.create_task(consume(last, tail_deadline, False))
    await asyncio.sleep(2)
    await write()
    await write()
    final = (
        await run.a.call("GET", f"/houses/{run.house_id}/events?after={start_seq}")
    )["events"]
    expected = [int(e["seq"]) for e in final]
    while time.monotonic() < tail_deadline and not set(expected) <= set(received):
        await asyncio.sleep(0.25)
    reconnect.cancel()
    try:
        await reconnect
    except (asyncio.CancelledError, CheckError):
        pass
    problems += sequence_problems(received, expected)
    if write_count < 4:
        problems.append(f"only {write_count} writes happened during the check")
    if problems:
        raise CheckError("\n".join(problems))
    how = (
        f"server rollover at {rollover_at:.0f} s"
        if rolled and rollover_at is not None
        else f"client reconnect after {held:.0f} s (server rollover needs --sse-seconds >= {SSE_ROLLOVER_S:.0f})"
    )
    return f"{len(received)} events {received[0] if received else '-'}..{received[-1] if received else '-'}, gapless, no duplicates; {how}; Last-Event-ID replay"


STEPS: list[Step] = [
    Step("health", step_health),
    Step("security headers + CSP", step_headers),
    Step("manifest, service worker, HTTPS", step_pwa),
    Step("sign-up by email code", step_signup),
    Step("create house", step_house, ("sign-up by email code",)),
    Step("2nd + 3rd user join by link", step_join, ("create house",)),
    Step("text-PDF bill via upload", step_pdf_bill, ("2nd + 3rd user join by link",)),
    Step("split", step_split, ("text-PDF bill via upload",)),
    Step("send", step_send, ("split",)),
    Step("photo bill via OCR path", step_photo_bill, ("2nd + 3rd user join by link",)),
    Step("payment", step_payment, ("send",)),
    Step("settle", step_settle, ("payment",)),
    Step("kitchen item", step_kitchen_item, ("2nd + 3rd user join by link",)),
    Step("ask -> add to split", step_ask_split, ("photo bill via OCR path",)),
    Step("push to a test subscription", step_push, ("send",)),
    Step(
        "19 MB PDF via client upload; 5 MB JSON -> 413",
        step_big_upload,
        ("2nd + 3rd user join by link",),
    ),
    Step("SSE across the 270 s rollover", step_sse, ("kitchen item",)),
]


def iter_steps(names: str | None) -> Iterator[Step]:
    wanted = {n.strip().lower() for n in names.split(",")} if names else None
    for s in STEPS:
        if wanted is None or s.name.lower() in wanted:
            yield s


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="KitchenTab launch check (BUILD_ALL §6)"
    )
    parser.add_argument("--target", required=True, help="local-vercel or prod")
    parser.add_argument("--base-url", help="override the base URL")
    parser.add_argument("--ca-file", help="trust this CA certificate (PEM)")
    parser.add_argument("--state-file", help=argparse.SUPPRESS)
    parser.add_argument(
        "--sse-seconds",
        type=float,
        help=f"how long to hold the SSE stream (default {DEFAULT_SSE_SECONDS:.0f}; the server rolls over at "
        f"{SSE_ROLLOVER_S:.0f} s; shorter values reconnect from the client instead)",
    )
    parser.add_argument(
        "--no-pull", action="store_true", help="TARGET=prod: never call the Vercel CLI"
    )
    args = parser.parse_args(argv)
    try:
        cfg = resolve_config(args)
    except CheckError as err:
        print(f"launch-check: {err}", file=sys.stderr)
        return 2
    return asyncio.run(Run(cfg).execute(list(iter_steps(None))))


if __name__ == "__main__":
    sys.exit(main())
