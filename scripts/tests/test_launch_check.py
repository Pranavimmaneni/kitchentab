"""Unit tests for scripts/launch_check.py and the local-vercel rehearsal config (ADR-0028).

The end-to-end run is `make launch-check TARGET=local-vercel` against `make local-vercel-up`;
these tests pin the pieces it relies on, without any server."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "deploy"))

import launch_check as lc
from deploylib import TEST_EMAIL_DOMAIN

GOOD_MANIFEST: dict[str, Any] = {
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
    "icons": [
        {
            "src": "/icons/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/icons/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/icons/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
    "screenshots": [
        {"src": "/s/kitchen.png", "sizes": "780x1688", "form_factor": "narrow"},
        {"src": "/s/split.png", "sizes": "780x1688", "form_factor": "narrow"},
    ],
    "shortcuts": [
        {"url": "/bills/new"},
        {"url": "/kitchen/fridge"},
        {"url": "/kitchen/requests"},
    ],
}

API_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
}
PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'nonce-abc' 'strict-dynamic'; "
    "frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


# ---- the 19 MB / 10-page PDF ------------------------------------------------------------------------


@pytest.mark.id("VERCEL-03", "EXTRACT-01")
def test_big_pdf_is_deterministic_19mb_10_pages_without_text() -> None:
    from pypdf import PdfReader

    data = lc.make_big_pdf()
    assert data == lc.make_big_pdf(), "same bytes every run"
    assert (
        hashlib.sha256(data).hexdigest()
        == hashlib.sha256(lc.make_big_pdf()).hexdigest()
    )
    assert (
        lc.BIG_PDF_MIN_BYTES <= len(data) <= lc.UPLOAD_LIMIT
    )  # 19 MiB .. 20 MiB (FR-10)
    assert len(data) > 4 * lc.BODY_LIMIT, (
        "far over the function body limit: only a client upload can carry it"
    )
    assert data.startswith(b"%PDF-")  # the API sniffs magic bytes
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == lc.BIG_PDF_PAGES == 10
    assert all((p.extract_text() or "").strip() == "" for p in reader.pages), (
        "a scan: needs OCR"
    )


def test_big_pdf_scales_with_its_arguments() -> None:
    from pypdf import PdfReader

    small = lc.make_big_pdf(pages=3, min_bytes=30_000)
    assert 30_000 <= len(small) < 60_000
    assert len(PdfReader(io.BytesIO(small)).pages) == 3


# ---- SSE ------------------------------------------------------------------------------------------------


@pytest.mark.id("VERCEL-01")
def test_sse_parser_handles_split_chunks_comments_and_ids() -> None:
    stream = (
        'retry: 1000\n\nevent: ready\ndata: {"seq":4}\n\n: keep-alive\n\n'
        'id: 5\ndata: {"seq":5,"type":"x"}\n\nid: 6\r\ndata: {"seq":6}\r\n\r\n'
        'event: rollover\ndata: {"seq":6}\n\n'
    )
    parser = lc.SseParser()
    events = []
    for i in range(0, len(stream), 7):  # arbitrary chunk boundaries
        events += parser.feed(stream[i : i + 7])
    assert [(e.event, e.id) for e in events] == [
        ("ready", None),
        (None, "5"),
        (None, "6"),
        ("rollover", None),
    ]
    assert events[0].json() == {"seq": 4}
    assert events[1].json()["type"] == "x"


@pytest.mark.id("VERCEL-01")
def test_sequence_problems_finds_missed_duplicated_and_reordered_events() -> None:
    assert lc.sequence_problems([5, 6, 7], [5, 6, 7]) == []
    assert any("missed" in p for p in lc.sequence_problems([5, 7], [5, 6, 7]))
    assert any("duplicate" in p for p in lc.sequence_problems([5, 6, 6, 7], [5, 6, 7]))
    assert any("out of order" in p for p in lc.sequence_problems([5, 7, 6], [5, 6, 7]))
    assert any("gaps" in p for p in lc.sequence_problems([5, 7], [5, 7]))
    assert any("doesn't know" in p for p in lc.sequence_problems([5, 6, 9], [5, 6]))


# ---- manifest, headers, cookies -----------------------------------------------------------------------


@pytest.mark.id("PWA-01")
def test_manifest_problems_follow_prd_20_1() -> None:
    assert lc.manifest_problems(GOOD_MANIFEST) == []
    bad = {
        **GOOD_MANIFEST,
        "display": "browser",
        "screenshots": [],
        "shortcuts": GOOD_MANIFEST["shortcuts"][:2],
    }
    bad["icons"] = [i for i in GOOD_MANIFEST["icons"] if i["purpose"] != "maskable"]
    problems = lc.manifest_problems(bad)
    assert any(p.startswith("display") for p in problems)
    assert any("narrow screenshots" in p for p in problems)
    assert any("/kitchen/requests" in p for p in problems)
    assert any("maskable" in p for p in problems)
    assert (
        lc.manifest_problems({**GOOD_MANIFEST, "theme_color": "#121210".lower()}) == []
    )


@pytest.mark.id("SEC-03")
def test_header_problems_for_pages_and_api() -> None:
    assert lc.header_problems(API_HEADERS, kind="api", require_hsts=False) == []
    assert lc.header_problems(PAGE_HEADERS, kind="page", require_hsts=False) == []
    assert any(
        "Strict-Transport-Security" in p
        for p in lc.header_problems(API_HEADERS, kind="api", require_hsts=True)
    )
    assert any(
        "no Content-Security-Policy" in p
        for p in lc.header_problems({}, kind="page", require_hsts=False)
    )
    unsafe = {
        **PAGE_HEADERS,
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'",
    }
    assert any(
        "unsafe-inline" in p
        for p in lc.header_problems(unsafe, kind="page", require_hsts=False)
    )
    cacheable = {**API_HEADERS, "Cache-Control": "public, max-age=60"}
    assert any(
        "no-store" in p
        for p in lc.header_problems(cacheable, kind="api", require_hsts=False)
    )
    framable = {k: v for k, v in API_HEADERS.items() if k != "X-Frame-Options"}
    framable["Content-Security-Policy"] = "default-src 'none'"
    assert any(
        "framed" in p
        for p in lc.header_problems(framable, kind="api", require_hsts=False)
    )


def test_session_cookie_must_be_secure_httponly_lax() -> None:
    good = [
        "kt_csrf=x; Path=/; Secure; SameSite=lax",
        "kt_session=abc; HttpOnly; Path=/; SameSite=lax; Secure",
    ]
    assert lc.session_cookie_problems(good) == []
    assert lc.session_cookie_problems(
        ["kt_session=abc; HttpOnly; Path=/; SameSite=lax"]
    ) == ["kt_session cookie lacks secure"]
    assert lc.session_cookie_problems([]) == ["sign-in set no kt_session cookie"]


@pytest.mark.id("VERCEL-10")
def test_every_user_is_tagged_for_prod_clean_test_data() -> None:
    email = lc.test_email("pranav", "abcd1234")
    assert (
        email.endswith("@" + TEST_EMAIL_DOMAIN)
        and TEST_EMAIL_DOMAIN == "launch-check.kitchentab.test"
    )
    assert email.split("+")[0] == "pranav"


# ---- target resolution ----------------------------------------------------------------------------------


def _args(**kw: Any) -> argparse.Namespace:
    base = {
        "target": "local-vercel",
        "base_url": None,
        "ca_file": None,
        "state_file": None,
        "sse_seconds": None,
        "no_pull": True,
    }
    return argparse.Namespace(**{**base, **kw})


@pytest.mark.id("VERCEL-10")
def test_local_vercel_reads_the_rehearsal_state(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "base_url": "https://localhost:8443/",
                "push_url": "http://localhost:4020",
                "sw_path": "/sw-next-lv.js",
                "secrets": {"TEST_INBOX_SECRET": "s" * 86},
            }
        ),
        encoding="utf-8",
    )
    cfg = lc.resolve_config(_args(state_file=str(state)), env={})
    assert (cfg.target, cfg.base_url, cfg.push_url, cfg.sw_path) == (
        "local-vercel",
        "https://localhost:8443",
        "http://localhost:4020",
        "/sw-next-lv.js",
    )
    assert cfg.inbox_secret == "s" * 86 and "s" * 86 not in repr(cfg), (
        "the secret never shows in a repr"
    )
    assert cfg.sse_seconds == lc.DEFAULT_SSE_SECONDS >= lc.SSE_ROLLOVER_S
    assert (
        lc.resolve_config(
            _args(state_file=str(state)), env={"LAUNCH_CHECK_SSE_SECONDS": "30"}
        ).sse_seconds
        == 30
    )
    with pytest.raises(lc.CheckError, match="local-vercel-up"):
        lc.resolve_config(_args(state_file=str(tmp_path / "missing.json")), env={})


@pytest.mark.id("VERCEL-10")
def test_prod_needs_an_explicit_https_url_and_the_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(lc, "DEPLOY_STATE", tmp_path / "none.json")
    with pytest.raises(lc.CheckError, match="production URL"):
        lc.resolve_config(_args(target="prod"), env={"TEST_INBOX_SECRET": "x" * 64})
    with pytest.raises(lc.CheckError, match="https production URL"):
        lc.resolve_config(
            _args(target="prod", base_url="https://localhost:8443"),
            env={"TEST_INBOX_SECRET": "x" * 64},
        )
    with pytest.raises(lc.CheckError, match="TEST_INBOX_SECRET"):
        lc.resolve_config(
            _args(target="prod", base_url="https://kitchentab.vercel.app"), env={}
        )
    cfg = lc.resolve_config(
        _args(target="prod"),
        env={
            "LAUNCH_CHECK_URL": "https://kitchentab.vercel.app/",
            "TEST_INBOX_SECRET": "x" * 64,
        },
    )
    assert (cfg.base_url, cfg.push_url, cfg.sw_path) == (
        "https://kitchentab.vercel.app",
        None,
        "/sw.js",
    )
    (tmp_path / "deploy.json").write_text(
        json.dumps({"production_url": "https://kt.example.app"}), encoding="utf-8"
    )
    monkeypatch.setattr(lc, "DEPLOY_STATE", tmp_path / "deploy.json")
    assert (
        lc.resolve_config(
            _args(target="prod"), env={"TEST_INBOX_SECRET": "x" * 64}
        ).base_url
        == "https://kt.example.app"
    )
    with pytest.raises(lc.CheckError, match="--target"):
        lc.resolve_config(_args(target="local"), env={})


def test_target_is_required_so_prod_is_never_the_default() -> None:
    with pytest.raises(SystemExit):
        lc.main([])


# ---- the runner -----------------------------------------------------------------------------------------


@pytest.mark.id("VERCEL-10")
def test_runner_prints_pass_fail_skip_with_timings_and_exits_nonzero_on_failure() -> (
    None
):
    cfg = lc.Config("local-vercel", "https://localhost:8443", "secret-" * 12)
    lines: list[str] = []

    async def ok(run: lc.Run) -> str:
        return "fine"

    async def bad(run: lc.Run) -> str:
        raise lc.CheckError("broken\nsecond line")

    async def manual(run: lc.Run) -> str:
        return "MANUAL: look at your phone"

    steps = [
        lc.Step("first", ok),
        lc.Step("second", bad),
        lc.Step("third", ok, ("second",)),
        lc.Step("fourth", manual, ("first",)),
    ]
    code = asyncio.run(lc.Run(cfg, out=lines.append).execute(steps))
    text = "\n".join(lines)
    assert code == 1
    assert re.search(r"PASS\s+1 first .* ms", text)
    assert re.search(r"FAIL\s+2 second", text) and "second line" in text
    assert re.search(r"SKIP\s+3 third", text) and "needs second" in text
    assert re.search(r"MANUAL\s+4 fourth", text)
    assert "1 passed, 1 failed, 1 skipped, 1 to check by hand" in text
    assert "secret-" not in text

    lines.clear()
    assert (
        asyncio.run(lc.Run(cfg, out=lines.append).execute([lc.Step("only", ok)])) == 0
    )


def test_step_order_matches_build_all_6_2() -> None:
    names = [s.name for s in lc.STEPS]
    assert names[0] == "health" and names[-1].startswith("SSE")
    for earlier, later in [
        ("sign-up by email code", "create house"),
        ("send", "payment"),
        ("payment", "settle"),
    ]:
        assert names.index(earlier) < names.index(later)
    known = set(names)
    assert all(n in known for s in lc.STEPS for n in s.needs), (
        "dependencies name real steps"
    )


# ---- the rehearsal's edge and compose config -------------------------------------------------------------


@pytest.mark.id("VERCEL-07")
def test_edge_routes_exactly_like_vercel_json() -> None:
    """The Caddyfile's handle blocks are vercel.json's rewrites, in the same order, same targets."""
    rewrites = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))[
        "rewrites"
    ]
    want = [
        (r["source"].replace("(.*)", "*"), r["destination"]["service"])
        for r in rewrites
    ]
    caddy = (ROOT / "infra" / "local-vercel" / "Caddyfile").read_text(encoding="utf-8")
    site = caddy.split(":{$LV_EDGE_PORT:8443} {", 1)[1].split("\n}\n", 1)[0]
    got = []
    for m in re.finditer(
        r"^\thandle( [^ {]+)? \{\n\t\treverse_proxy (\S+)", site, re.MULTILINE
    ):
        path = (m.group(1) or " /*").strip()
        got.append(
            (
                path,
                "web"
                if "WEB" in m.group(2)
                else "api"
                if "API" in m.group(2)
                else m.group(2),
            )
        )
    assert got == want


@pytest.mark.id("VERCEL-03")
def test_edge_enforces_body_and_duration_limits() -> None:
    caddy = (ROOT / "infra" / "local-vercel" / "Caddyfile").read_text(encoding="utf-8")
    assert "int({http.request.header.Content-Length}) > 4500000" in caddy
    assert "max_size 4500000" in caddy and '" 413' in caddy
    assert "write {$LV_MAX_DURATION:300s}" in caddy


def test_rehearsal_images_are_pinned_and_launch_check_is_not_pending() -> None:
    compose = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    lv = compose.split("lv-pgbouncer:", 1)[1].split("\nvolumes:", 1)[0]
    images = re.findall(r"image: (\S+)", lv)
    assert images and all(
        re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", i) for i in images
    ), images
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    pending = re.search(r"^PENDING \?=(.*(?:\\\n.*)*)", makefile, re.MULTILINE)
    assert pending and "launch-check" not in pending.group(1)
    assert re.search(r"^launch-check: ##", makefile, re.MULTILINE)
