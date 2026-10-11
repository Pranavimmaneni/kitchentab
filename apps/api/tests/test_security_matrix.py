"""SEC-01: the HTTP authorization matrix, every /api/v1 route x six roles (TEST_PLAN 3.10).

`tests/test_policy.py` proves `decide` cell by cell; this suite proves the routes actually ask it,
over real HTTP against real Postgres. Each row lists the roles that pass authorization; business
rules may still refuse them (409 already asked, 422 the payer doesn't pay themselves, ...), so an
allowed cell only has to avoid 401/403/404 and 5xx. Every other cell must be 401 (signed out) or
403/404 (signed in), never 2xx and never 5xx.

A new route fails `test_SEC_01_every_route_is_in_the_matrix` until someone adds its row here and
decides who may call it. Test-only routes (/__test, /api/test) are excluded (SEC-11 covers them);
the cron routes authenticate with a bearer secret and are tested on their own below.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.routing import iter_route_contexts

from app.main import create_app
from app.services.notify import TYPE_LABELS, unsubscribe_sig
from app.settings import Settings
from tests.harness.api import Api
from tests.harness.security_world import (
    ADMIN,
    ANON,
    EVERYONE,
    GUEST,
    HOUSE,
    JPEG,
    MEMBER,
    OUTSIDER,
    PAYER,
    ROLES,
    SIGNED_IN,
    HouseWorld,
    World,
    build_world,
)
from tests.harness.tags import fr, tid

ApiFactory = Callable[[], Api]
Ids = Callable[[HouseWorld], str]
Body = Callable[[World, str], Any]
Version = Callable[[HouseWorld], Awaitable[int]]

PREFIX = "/api/v1"
# Built by the receipts work in parallel; a row is skipped only while its route doesn't exist yet.
PENDING = {
    ("POST", "/api/v1/uploads/authorize"),
    ("POST", "/api/v1/bills/{bill_id}/receipts"),
    ("GET", "/api/v1/bills/{bill_id}/receipts"),
    ("POST", "/api/v1/bills/{bill_id}/ocr"),
    ("GET", "/api/v1/receipts/{receipt_id}/view"),
    ("POST", "/api/v1/bills/{bill_id}/extractions/{extraction_id}/apply"),
    ("POST", "/api/v1/bills/{bill_id}/extractions/{extraction_id}/dismiss"),
}
RECEIPT_FLOW = {("POST", "/api/v1/uploads/authorize"), ("POST", "/api/v1/bills/{bill_id}/receipts")}
OFFER_FLOW = {
    ("POST", "/api/v1/bills/{bill_id}/ocr"),
    ("POST", "/api/v1/bills/{bill_id}/extractions/{extraction_id}/apply"),
}
EXCLUDED_PREFIXES = ("/api/v1/cron/",)  # bearer CRON_SECRET: test_SEC_01_cron_routes_need_the_secret
# Allowed roles run last, in this order, so a success can't change what a denied role sees.
ALLOWED_ORDER = (ANON, GUEST, OUTSIDER, MEMBER, PAYER, ADMIN)


@dataclass(frozen=True)
class Row:
    method: str
    path: str
    allowed: frozenset[str]
    ids: dict[str, Ids] = field(default_factory=dict)
    body: Body | None = None
    if_match: Version | None = None
    query: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def url(self, h: HouseWorld) -> str:
        path = self.path
        for name, get in self.ids.items():
            path = path.replace("{" + name + "}", get(h))
        assert "{" not in path, f"unfilled path parameter in {path}"
        return path + self.query


def r(
    method: str,
    path: str,
    allowed: frozenset[str],
    *,
    body: Body | None = None,
    if_match: Version | None = None,
    query: str = "",
    **ids: Ids,
) -> Row:
    return Row(method, PREFIX + path, allowed, ids, body, if_match, query)


def const(value: Any) -> Body:
    return lambda _w, _role: value


def unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


# ---- ids ------------------------------------------------------------------------------------------
H: Ids = lambda h: h.house_id  # noqa: E731
DRAFT: Ids = lambda h: h.draft_id  # noqa: E731
OPEN: Ids = lambda h: h.open_id  # noqa: E731
DRAFT_ITEM: Ids = lambda h: h.draft_items[0]  # noqa: E731
PLACEHOLDER: Ids = lambda h: h.placeholder_member  # noqa: E731
CLAIMED: Ids = lambda h: h.member_member  # noqa: E731
INV: Ids = lambda h: h.inv_item_id  # noqa: E731
ASK: Ids = lambda h: h.ask_id  # noqa: E731
PAYMENT: Ids = lambda h: h.payment_id  # noqa: E731
INVITE: Ids = lambda h: h.invite_token  # noqa: E731
LINK: Ids = lambda h: h.link_token  # noqa: E731
SUB: Ids = lambda h: h.sub_id  # noqa: E731
IDENTITY: Ids = lambda h: h.admin_identity_id  # noqa: E731
RECEIPT: Ids = lambda h: h.receipt_id  # noqa: E731
EXTRACTION: Ids = lambda h: h.extraction_id  # noqa: E731


def _unsubscribe_query(h: HouseWorld) -> str:
    """A validly signed one-click unsubscribe link for the admin (EMAIL-02)."""
    from app.settings import get_settings

    user = h.who[ADMIN].user_id
    assert user
    kind = next(iter(TYPE_LABELS))
    sig = unsubscribe_sig(get_settings().token_hash_pepper.get_secret_value(), uuid.UUID(user), kind)
    return f"?u={user}&t={kind}&s={sig}"


PUSH_KEYS = {"p256dh": "B" * 87, "auth": "A" * 22}

TABLE: list[Row] = [
    # health, docs, sign-in plumbing: public
    r("GET", "/health", EVERYONE),
    r("GET", "/health/ready", EVERYONE),
    r("GET", "/openapi.json", EVERYONE),
    r("GET", "/docs", EVERYONE),
    r("GET", "/auth/methods", EVERYONE),
    r("GET", "/auth/csrf", EVERYONE),
    r(
        "POST",
        "/auth/otp/send",
        EVERYONE,
        body=lambda w, role: {"channel": "email", "destination": f"otp-{role}-{w.home.tag}@example.com"},
    ),
    r(
        "POST",
        "/auth/otp/verify",
        EVERYONE,
        body=lambda w, role: {"channel": "email", "destination": f"nocode-{role}@example.com", "code": "123456"},
    ),
    r("GET", "/auth/google/start", EVERYONE),
    r("GET", "/auth/google/callback", EVERYONE, query="?code=probe&state=probe"),
    r("POST", "/auth/logout", EVERYONE),
    r("POST", "/auth/logout-all", SIGNED_IN),
    # the signed-in user's own things
    r("GET", "/me", SIGNED_IN),
    r("PATCH", "/me", SIGNED_IN, body=const({"display_name": "Probe Person"})),
    r("DELETE", "/me", SIGNED_IN, body=const({"confirm": "DELETE"})),
    r("GET", "/me/identities", SIGNED_IN),
    r(
        "POST",
        "/me/identities/start",
        SIGNED_IN,
        body=lambda w, role: {"channel": "email", "destination": f"extra-{role}-{w.home.tag}@example.com"},
    ),
    r(
        "POST",
        "/me/identities/verify",
        SIGNED_IN,
        body=lambda w, role: {
            "channel": "email",
            "destination": f"extra-{role}-{w.home.tag}@example.com",
            "code": "123456",
        },
    ),
    r("DELETE", "/me/identities/{identity_id}", frozenset({ADMIN}), identity_id=IDENTITY),
    r("GET", "/me/payment-handles", SIGNED_IN),
    r("PUT", "/me/payment-handles", SIGNED_IN, body=const({"venmo": "probe-handle"})),
    r("GET", "/me/export", SIGNED_IN),
    r("GET", "/me/notification-prefs", SIGNED_IN),
    r("PUT", "/me/notification-prefs", SIGNED_IN, body=const({"push": {"tab": True}})),
    r("GET", "/me/inbox", SIGNED_IN),
    r("POST", "/me/inbox/read", SIGNED_IN, body=const({"all": True})),
    r("GET", "/push/config", EVERYONE),
    r(
        "POST",
        "/push/subscriptions",
        SIGNED_IN,
        body=lambda _w, role: {"endpoint": f"https://push.test/send/{unique(role)}", "keys": PUSH_KEYS},
    ),
    r("GET", "/push/subscriptions", SIGNED_IN),
    r("DELETE", "/push/subscriptions/{sub_id}", frozenset({MEMBER}), sub_id=SUB),
    r("POST", "/push/test", SIGNED_IN),
    # houses and members (admin-only management, PRD §7)
    r("POST", "/houses", SIGNED_IN, body=lambda _w, role: {"name": unique("Probe "), "roommates": []}),
    r("GET", "/houses", SIGNED_IN),
    r("GET", "/houses/{house_id}", HOUSE, house_id=H),
    r(
        "PATCH",
        "/houses/{house_id}",
        frozenset({ADMIN}),
        body=const({"name": "Renamed house"}),
        if_match=lambda h: h.house_version(),
        house_id=H,
    ),
    r("POST", "/houses/{house_id}/invite/rotate", frozenset({ADMIN}), house_id=H),
    r(
        "POST",
        "/houses/{house_id}/members",
        frozenset({ADMIN}),
        body=lambda _w, _r: {"name": unique("New")},
        house_id=H,
    ),
    r(
        "PATCH",
        "/houses/{house_id}/members/{member_id}",
        frozenset({ADMIN}),
        body=const({"name": "Kabir K"}),
        if_match=lambda h: h.member_version(h.placeholder_member),
        house_id=H,
        member_id=PLACEHOLDER,
    ),
    r("DELETE", "/houses/{house_id}/members/{member_id}", frozenset({ADMIN}), house_id=H, member_id=PLACEHOLDER),
    r("POST", "/houses/{house_id}/members/{member_id}/unclaim", frozenset({ADMIN}), house_id=H, member_id=CLAIMED),
    r("POST", "/houses/{house_id}/members/{member_id}/make-admin", frozenset({ADMIN}), house_id=H, member_id=CLAIMED),
    r("POST", "/houses/{house_id}/leave", HOUSE, house_id=H),
    r("GET", "/join/{token}", EVERYONE, token=INVITE),
    r("POST", "/join/{token}/claim", SIGNED_IN, body=lambda _w, _r: {"new_name": unique("Joiner")}, token=INVITE),
    # bills (the payer edits; members read)
    r("POST", "/houses/{house_id}/bills", HOUSE, body=const({"store_name": "Probe store"}), house_id=H),
    r("GET", "/houses/{house_id}/bills", HOUSE, house_id=H),
    r("GET", "/bills/{bill_id}", HOUSE, bill_id=OPEN),
    r(
        "PATCH",
        "/bills/{bill_id}",
        frozenset({PAYER}),
        body=const({"store_name": "Edited store"}),
        if_match=lambda h: h.bill_version(h.draft_id),
        bill_id=DRAFT,
    ),
    r("DELETE", "/bills/{bill_id}", frozenset({PAYER}), bill_id=DRAFT),
    r(
        "POST",
        "/bills/{bill_id}/reconcile-override",
        frozenset({PAYER}),
        body=const({"reason": "Printed total is right"}),
        bill_id=DRAFT,
    ),
    r(
        "POST",
        "/bills/{bill_id}/items",
        frozenset({PAYER}),
        body=const({"name": "Probe item", "line_total_cents": 100}),
        bill_id=DRAFT,
    ),
    r(
        "PATCH",
        "/bills/{bill_id}/items/{item_id}",
        frozenset({PAYER}),
        body=const({"name": "Edited item"}),
        if_match=lambda h: h.item_version(h.draft_id, h.draft_items[0]),
        bill_id=DRAFT,
        item_id=DRAFT_ITEM,
    ),
    r("DELETE", "/bills/{bill_id}/items/{item_id}", frozenset({PAYER}), bill_id=DRAFT, item_id=DRAFT_ITEM),
    r(
        "PUT",
        "/bills/{bill_id}/items/order",
        frozenset({PAYER}),
        body=lambda w, _r: {"item_ids": list(reversed(w.home.draft_items))},
        bill_id=DRAFT,
    ),
    r(
        "POST",
        "/bills/{bill_id}/items/merge",
        frozenset({PAYER}),
        body=lambda w, _r: {"item_ids": w.home.draft_items},
        bill_id=DRAFT,
    ),
    r(
        "POST",
        "/bills/{bill_id}/items/{item_id}/split",
        frozenset({PAYER}),
        body=const({"parts": [{"line_total_cents": 100}, {"line_total_cents": 200}]}),
        bill_id=DRAFT,
        item_id=DRAFT_ITEM,
    ),
    # The payer edits anyone's ticks; a member only their own (FR-32, FR-33): the admin can't
    # untick Meera.
    r(
        "PUT",
        "/bills/{bill_id}/allocations",
        frozenset({PAYER, MEMBER}),
        body=lambda w, _r: {
            "changes": [{"item_id": w.home.open_items[0], "participant": w.home.key(w.home.member_member), "on": False}]
        },
        bill_id=OPEN,
    ),
    r("POST", "/bills/{bill_id}/allocations/fill-rest", frozenset({PAYER}), bill_id=DRAFT),
    r(
        "PUT",
        "/bills/{bill_id}/exclusions/{member_id}",
        frozenset({PAYER}),
        body=const({"excluded": True}),
        bill_id=DRAFT,
        member_id=CLAIMED,
    ),
    r("POST", "/bills/{bill_id}/guests", frozenset({PAYER}), body=const({"name": "Probe guest"}), bill_id=DRAFT),
    r("GET", "/bills/{bill_id}/preview-shares", HOUSE, bill_id=OPEN),
    r("POST", "/bills/{bill_id}/send", frozenset({PAYER}), bill_id=DRAFT),
    r("GET", "/b/{token}", EVERYONE, token=LINK),
    r("POST", "/b/{token}/claim", SIGNED_IN, body=lambda _w, _r: {"new_name": unique("Linker")}, token=LINK),
    # receipts (PRD §7: the draft's payer uploads; house members view)
    r(
        "POST",
        "/uploads/authorize",
        frozenset({PAYER}),
        body=lambda w, _r: {"bill_id": w.home.draft_id, "mime": "image/jpeg", "bytes": 1000},
    ),
    r(
        "POST",
        "/bills/{bill_id}/receipts",
        frozenset({PAYER}),
        body=lambda w, _r: {"storage_key": w.home.spare_receipt_key, "mime": "image/jpeg", "bytes": len(JPEG)},
        bill_id=DRAFT,
    ),
    r("GET", "/bills/{bill_id}/receipts", HOUSE, bill_id=DRAFT),
    r(
        "POST",
        "/bills/{bill_id}/ocr",
        frozenset({PAYER}),
        body=const({"parts": [{"pages": [{"lines": []}]}]}),
        bill_id=DRAFT,
    ),
    r("GET", "/receipts/{receipt_id}/view", HOUSE, receipt_id=RECEIPT),
    r(
        "POST",
        "/bills/{bill_id}/extractions/{extraction_id}/apply",
        frozenset({PAYER}),
        bill_id=DRAFT,
        extraction_id=EXTRACTION,
    ),
    r(
        "POST",
        "/bills/{bill_id}/extractions/{extraction_id}/dismiss",
        frozenset({PAYER}),
        bill_id=DRAFT,
        extraction_id=EXTRACTION,
    ),
    # open-bill life (members pay; the payer confirms, settles, reopens, voids, reminds)
    r("POST", "/bills/{bill_id}/payments", HOUSE, body=const({"amount_cents": 50, "method": "cash"}), bill_id=OPEN),
    r("POST", "/payments/{payment_id}/confirm", frozenset({PAYER}), payment_id=PAYMENT),
    r(
        "POST",
        "/payments/{payment_id}/reject",
        frozenset({PAYER}),
        body=const({"note": "Not seen"}),
        payment_id=PAYMENT,
    ),
    r("POST", "/bills/{bill_id}/settle", frozenset({PAYER}), body=const({"confirm_outstanding": True}), bill_id=OPEN),
    r("POST", "/bills/{bill_id}/reopen", frozenset({PAYER}), bill_id=OPEN),
    r("POST", "/bills/{bill_id}/void", frozenset({PAYER}), body=const({"reason": "Wrong store"}), bill_id=OPEN),
    r(
        "POST",
        "/bills/{bill_id}/remind",
        frozenset({PAYER}),
        body=lambda w, _r: {"member_id": w.home.member_member},
        bill_id=OPEN,
    ),
    r("GET", "/bills/{bill_id}/money", HOUSE, bill_id=OPEN),
    r("PUT", "/bills/{bill_id}/seen", HOUSE, body=const({"version": 1}), bill_id=OPEN),
    r("GET", "/bills/{bill_id}/activity", HOUSE, bill_id=OPEN),
    r("GET", "/bills/{bill_id}/export.csv", HOUSE, bill_id=OPEN),
    r("GET", "/houses/{house_id}/balances", HOUSE, house_id=H),
    r("GET", "/houses/{house_id}/tabs", HOUSE, house_id=H),
    r("GET", "/houses/{house_id}/search", HOUSE, query="?q=milk", house_id=H),
    r("GET", "/houses/{house_id}/events", HOUSE, query="?after=0", house_id=H),
    # fridge and pantry (owners act on their items; non-owners ask; the asker cancels)
    r("GET", "/houses/{house_id}/kitchen", HOUSE, house_id=H),
    r("GET", "/houses/{house_id}/inventory", HOUSE, house_id=H),
    r("POST", "/houses/{house_id}/inventory", HOUSE, body=const({"name": "Probe soup"}), house_id=H),
    r(
        "PATCH",
        "/inventory/{item_id}",
        frozenset({MEMBER}),
        body=const({"name": "Edited curry"}),
        if_match=lambda h: h.inventory_version(h.inv_item_id),
        item_id=INV,
    ),
    r("PUT", "/inventory/{item_id}/sharing", frozenset({MEMBER}), body=const({"open": True}), item_id=INV),
    r("POST", "/inventory/{item_id}/used-up", frozenset({MEMBER}), item_id=INV),
    r("POST", "/inventory/{item_id}/restore", frozenset({MEMBER}), item_id=INV),
    r("POST", "/inventory/{item_id}/still-good", frozenset({MEMBER}), item_id=INV),
    r("POST", "/inventory/{item_id}/requests", HOUSE, body=const({"amount": "half"}), item_id=INV),
    r("GET", "/houses/{house_id}/requests", HOUSE, house_id=H),
    r("GET", "/requests/{request_id}/split-preview", HOUSE, request_id=ASK),
    r("GET", "/requests/{request_id}/answer-preview", HOUSE, request_id=ASK),
    r("POST", "/requests/{request_id}/answer", frozenset({MEMBER}), body=const({"decision": "free"}), request_id=ASK),
    r("POST", "/requests/{request_id}/undo", frozenset({MEMBER}), request_id=ASK),
    r("POST", "/requests/{request_id}/cancel", frozenset({ADMIN}), request_id=ASK),
    r("GET", "/houses/{house_id}/kitchen/history", HOUSE, house_id=H),
    # kitchen gestures: any member moves any item; everyone sets only their own order
    r(
        "POST",
        "/houses/{house_id}/items/move",
        HOUSE,
        body=lambda w, _r: {"moves": [{"item_id": w.home.inv_item_id, "to": "pantry"}]},
        house_id=H,
    ),
    r(
        "PUT",
        "/houses/{house_id}/me/item-order/{location}",
        HOUSE,
        body=const({"item_ids": []}),
        house_id=H,
        location=lambda _h: "fridge",
    ),
    # email one-click unsubscribe: a signed link, no session
    r("GET", "/email/unsubscribe", EVERYONE, query="{unsubscribe}"),
    r("POST", "/email/unsubscribe", EVERYONE, query="{unsubscribe}"),
]
TABLE_BY_KEY = {row.key: row for row in TABLE}
assert len(TABLE_BY_KEY) == len(TABLE), "duplicate rows in the matrix"


def api_routes(settings: Settings) -> set[tuple[str, str]]:
    """Every (method, path) the app serves under /api/v1, minus the bearer-secret cron routes."""
    out: set[tuple[str, str]] = set()
    # FastAPI keeps included routers lazily (`_IncludedRouter` has no path): walk the effective routes.
    for route in iter_route_contexts(create_app(settings).routes):
        path = route.path or ""
        if not path.startswith(PREFIX + "/") or path.startswith(EXCLUDED_PREFIXES):
            continue
        for method in route.methods or ():
            if method != "HEAD":
                out.add((method, path))
    return out


@tid("SEC-01")
def test_SEC_01_every_route_is_in_the_matrix(settings: Settings) -> None:
    served = api_routes(settings)
    unlisted = sorted(served - set(TABLE_BY_KEY))
    assert not unlisted, f"routes with no SEC-01 row (decide who may call them): {unlisted}"
    stale = sorted(set(TABLE_BY_KEY) - served - PENDING)
    assert not stale, f"SEC-01 rows for routes that don't exist: {stale}"


def _expect(row: Row, role: str, status: int) -> str | None:
    if status >= 500:
        return f"{role}: {status} (server error)"
    if role in row.allowed:
        if status in (401, 403, 404):
            return f"{role}: allowed but got {status}"
        return None
    expected = {401} if role == ANON else {403, 404}
    if status not in expected:
        return f"{role}: denied but got {status} (want {sorted(expected)})"
    return None


async def call(row: Row, world: World, role: str) -> httpx.Response:
    who = world.who[role]
    url = row.url(world.home)
    if "{unsubscribe}" in url:
        url = url.replace("{unsubscribe}", _unsubscribe_query(world.home))
    version = await row.if_match(world.home) if row.if_match else None
    body = row.body(world, role) if row.body else None
    if row.method == "GET":
        return await who.http.get(url)
    # Transitions (POST, DELETE /bills/{id}) need an Idempotency-Key; set-state writes ignore it.
    return await who.call(row.method, url, body, if_match=version, idem=True)


async def check_row(row: Row, world: World) -> list[str]:
    """Denied roles first, then allowed ones, so a success can't change what a denied role sees."""
    order = [x for x in ROLES if x not in row.allowed] + [x for x in ALLOWED_ORDER if x in row.allowed]
    problems: list[str] = []
    for role in order:
        resp = await call(row, world, role)
        problem = _expect(row, role, resp.status_code)
        if problem:
            problems.append(f"{row.method} {row.path} {problem} {resp.text[:200]}")
    return problems


async def world_for(api: ApiFactory, app: Any, settings: Settings) -> tuple[World, set[tuple[str, str]]]:
    served = api_routes(settings)
    world = await build_world(api, app, receipts=served >= RECEIPT_FLOW, offers=served >= OFFER_FLOW)
    return world, served


READS = [row for row in TABLE if row.method == "GET"]
WRITES = [row for row in TABLE if row.method != "GET"]
FRS = ("FR-04", "FR-06", "FR-07", "FR-09", "FR-18", "FR-32", "FR-33", "FR-37", "FR-39", "FR-42", "FR-69", "FR-76")


@tid("SEC-01")
@fr(*FRS)
async def test_SEC_01_authorization_matrix_reads(api: ApiFactory, app: Any, settings: Settings) -> None:
    """Every GET row against one shared world (reads change nothing another row depends on)."""
    world, served = await world_for(api, app, settings)
    problems: list[str] = []
    for row in READS:
        if row.key not in served:
            assert row.key in PENDING, f"{row.key} is in the matrix but not served"
            continue
        problems += await check_row(row, world)
    assert not problems, "\n".join(problems)


@tid("SEC-01")
@fr(*FRS)
@pytest.mark.parametrize("row", WRITES, ids=[f"{row.method} {row.path[len(PREFIX) :]}" for row in WRITES])
async def test_SEC_01_authorization_matrix_writes(row: Row, api: ApiFactory, app: Any, settings: Settings) -> None:
    """One fresh world per write row: an allowed write may delete, settle or transfer things."""
    if row.key not in api_routes(settings):
        assert row.key in PENDING, f"{row.key} is in the matrix but not served"
        pytest.skip(f"{row.method} {row.path} isn't built yet (receipts work in progress)")
    world, _ = await world_for(api, app, settings)
    problems = await check_row(row, world)
    assert not problems, "\n".join(problems)


@tid("SEC-01")
async def test_SEC_01_cron_routes_need_the_secret(api: ApiFactory, settings: Settings) -> None:
    """Bearer CRON_SECRET (ADR-0013): no header, a wrong secret, a session or a cookie never runs it."""
    secret = settings.cron_secret
    assert secret is not None
    member = api()
    await member.sign_in(f"cron-{uuid.uuid4().hex[:8]}@example.com")
    for method, path in (("GET", "/api/v1/cron/daily"), ("POST", "/api/v1/cron/drain")):
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": secret.get_secret_value()}):
            resp = await member.http.request(method, path, headers=headers)
            assert resp.status_code == 401, (method, path, headers.keys(), resp.status_code)
        ok = await member.http.request(method, path, headers={"Authorization": f"Bearer {secret.get_secret_value()}"})
        assert ok.status_code == 200, ok.text
