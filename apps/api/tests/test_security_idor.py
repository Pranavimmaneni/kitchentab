"""SEC-02: insecure direct object references (TEST_PLAN 3.10).

Two fully populated houses. The admin, payer and member of house 1 use house 2's ids (house, bills,
items, payment, inventory item, ask, members, receipt, scan, push subscription, sign-in method):

* every SEC-01 route with an id in its path, filled with house 2's ids, answers 403 or 404 and never
  shows house 2's data;
* house-1 routes given house-2 ids in the body or mixed into the path (my tab, their item; my
  house, their member as an owner or a payer) are refused with a 4xx and change nothing;
* afterwards house 2 reads exactly as it did before.

Capability tokens (/join/{token}, /b/{token}) are not ids: holding one is the permission (LINK-*).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from tests.harness.api import Api
from tests.harness.security_world import (
    ADMIN,
    JPEG,
    MEMBER,
    PAYER,
    HouseWorld,
    World,
    blob_storage,
    build_house,
)
from tests.harness.tags import tid
from tests.test_security_matrix import OFFER_FLOW, PENDING, RECEIPT_FLOW, TABLE, Row, api_routes, call

ApiFactory = Callable[[], Api]
CALLERS = (ADMIN, PAYER, MEMBER)
REFUSED = {400, 403, 404, 409, 422}  # a foreign id in a body may fail validation instead of policy
CAPABILITY_PARAMS = {"token"}
STRANGER = "stranger"  # signed in, in no house yet, holding house 1's links


def _leaks(resp: httpx.Response, other: HouseWorld) -> list[str]:
    return [name for name in other.names if name in resp.text]


async def _snapshot(h: HouseWorld) -> str:
    """Everything house 2's own people can see, read by them."""
    admin, payer, member = h.who[ADMIN], h.who[PAYER], h.who[MEMBER]
    reads = [
        (admin, f"/api/v1/houses/{h.house_id}"),
        (payer, f"/api/v1/bills/{h.draft_id}"),
        (payer, f"/api/v1/bills/{h.open_id}"),
        (payer, f"/api/v1/bills/{h.open_id}/money"),
        (payer, f"/api/v1/bills/{h.open_id}/activity?limit=50"),
        (member, f"/api/v1/houses/{h.house_id}/inventory?place=fridge"),
        (member, f"/api/v1/houses/{h.house_id}/inventory?place=pantry"),
        (member, f"/api/v1/houses/{h.house_id}/requests?box=in"),
        (admin, f"/api/v1/houses/{h.house_id}/requests?box=out"),
        (member, "/api/v1/push/subscriptions"),
        (admin, "/api/v1/me/identities"),
        (payer, f"/api/v1/houses/{h.house_id}/balances"),
    ]
    if h.receipt_id:
        reads.append((payer, f"/api/v1/bills/{h.draft_id}/receipts"))
    out = {}
    for who, url in reads:
        resp = await who.get(url)
        assert resp.status_code == 200, (url, resp.text)
        out[url] = resp.json()
    return json.dumps(out, sort_keys=True)


def _path_rows(served: set[tuple[str, str]]) -> list[Row]:
    rows = []
    for row in TABLE:
        if row.key not in served:
            assert row.key in PENDING, row.key
            continue
        ids = set(row.ids) - CAPABILITY_PARAMS
        if ids or row.path.endswith("/uploads/authorize"):  # authorize names the bill in its body
            rows.append(row)
    return rows


def _mixed_probes(h1: HouseWorld, h2: HouseWorld) -> list[tuple[str, str, str, Any]]:
    """(caller, method, url, body): house-1 paths with house-2 ids in the body or a second path id."""
    m2 = h2.placeholder_member
    probes: list[tuple[str, str, str, Any]] = [
        (PAYER, "PATCH", f"/api/v1/bills/{h1.draft_id}/items/{h2.draft_items[0]}", {"name": "Mine now"}),
        (PAYER, "DELETE", f"/api/v1/bills/{h1.draft_id}/items/{h2.draft_items[0]}", None),
        (
            PAYER,
            "POST",
            f"/api/v1/bills/{h1.draft_id}/items/{h2.draft_items[0]}/split",
            {"parts": [{"line_total_cents": 100}, {"line_total_cents": 200}]},
        ),
        (
            PAYER,
            "POST",
            f"/api/v1/bills/{h1.draft_id}/items/merge",
            {"item_ids": [h1.draft_items[0], h2.draft_items[0]]},
        ),
        (PAYER, "PUT", f"/api/v1/bills/{h1.draft_id}/items/order", {"item_ids": h2.draft_items}),
        (
            PAYER,
            "PUT",
            f"/api/v1/bills/{h1.open_id}/allocations",
            {"changes": [{"item_id": h2.open_items[0], "participant": h1.key(h1.member_member), "on": True}]},
        ),
        (
            PAYER,
            "PUT",
            f"/api/v1/bills/{h1.open_id}/allocations",
            {"changes": [{"item_id": h1.open_items[0], "participant": h2.key(m2), "on": True}]},
        ),
        (
            PAYER,
            "PUT",
            f"/api/v1/bills/{h1.open_id}/allocations",
            {"changes": [{"item_id": h1.open_items[0], "participant": f"g:{h2.guest_id}", "on": True}]},
        ),
        (PAYER, "PUT", f"/api/v1/bills/{h1.draft_id}/exclusions/{m2}", {"excluded": True}),
        (PAYER, "POST", f"/api/v1/bills/{h1.open_id}/remind", {"member_id": h2.member_member}),
        (PAYER, "POST", f"/api/v1/houses/{h1.house_id}/bills", {"store_name": "Theirs", "payer_member_id": m2}),
        (PAYER, "PATCH", f"/api/v1/bills/{h1.draft_id}", {"payer_member_id": m2}),
        (
            PAYER,
            "POST",
            f"/api/v1/bills/{h1.open_id}/payments",
            {"amount_cents": 100, "method": "cash", "participant": h2.key(h2.member_member)},
        ),
        (
            PAYER,
            "POST",
            f"/api/v1/bills/{h1.open_id}/payments",
            {"amount_cents": 100, "method": "cash", "participant": f"g:{h2.guest_id}"},
        ),
        (ADMIN, "PATCH", f"/api/v1/houses/{h1.house_id}/members/{m2}", {"name": "Taken over"}),
        (ADMIN, "DELETE", f"/api/v1/houses/{h1.house_id}/members/{m2}", None),
        (ADMIN, "POST", f"/api/v1/houses/{h1.house_id}/members/{h2.member_member}/unclaim", None),
        (ADMIN, "POST", f"/api/v1/houses/{h1.house_id}/members/{h2.member_member}/make-admin", None),
        (STRANGER, "POST", f"/api/v1/join/{h1.invite_token}/claim", {"member_id": m2}),
        (STRANGER, "POST", f"/api/v1/b/{h1.link_token}/claim", {"member_id": m2}),
    ]
    if h1.receipt_id and h2.receipt_id:
        probes += [
            (
                PAYER,
                "POST",
                f"/api/v1/bills/{h1.draft_id}/ocr",
                {"receipt_id": h2.receipt_id, "parts": [{"pages": [{}]}]},
            ),
            (
                PAYER,
                "POST",
                f"/api/v1/bills/{h1.draft_id}/receipts",
                {"storage_key": h2.spare_receipt_key, "mime": "image/jpeg", "bytes": len(JPEG)},
            ),
            (
                PAYER,
                "POST",
                "/api/v1/uploads/authorize",
                {"bill_id": h1.draft_id, "mime": "image/jpeg", "bytes": 100, "storage_key": h2.spare_receipt_key},
            ),
        ]
    if h2.extraction_id:
        probes += [
            (PAYER, "POST", f"/api/v1/bills/{h1.draft_id}/extractions/{h2.extraction_id}/apply", None),
            (PAYER, "POST", f"/api/v1/bills/{h1.draft_id}/extractions/{h2.extraction_id}/dismiss", None),
        ]
    # Last: if these succeed they make a house-2 member an owner in house 1, which breaks house 1's views.
    probes += [
        (MEMBER, "PATCH", f"/api/v1/inventory/{h1.inv_item_id}", {"owner_member_ids": [h2.member_member]}),
        (MEMBER, "POST", f"/api/v1/houses/{h1.house_id}/inventory", {"name": "Not yours", "owner_member_ids": [m2]}),
    ]
    return probes


async def _if_match_for(h1: HouseWorld, method: str, url: str) -> int | None:
    """Guarded edits get the right version of the house-1 row, so only the foreign id can fail them."""
    if method != "PATCH":
        return None
    if "/items/" in url:
        return await h1.item_version(h1.draft_id, h1.draft_items[0])
    if "/members/" in url:
        return await h1.member_version(h1.placeholder_member)
    if "/inventory/" in url:
        return await h1.inventory_version(h1.inv_item_id)
    return await h1.bill_version(h1.draft_id)


@tid("SEC-02")
async def test_SEC_02_another_houses_ids_are_rejected_everywhere(api: ApiFactory, app: Any, settings: Any) -> None:
    served = api_routes(settings)
    receipts, offers = served >= RECEIPT_FLOW, served >= OFFER_FLOW
    storage = blob_storage() if receipts else None
    app.state.storage = storage
    h1 = await build_house(api, receipts=receipts, offers=offers, storage=storage)
    h2 = await build_house(api, receipts=receipts, offers=offers, storage=storage)
    stranger = api()
    await stranger.sign_in(f"stranger-{h1.tag}@example.com")
    h1.who[STRANGER] = stranger
    before = await _snapshot(h2)
    problems: list[str] = []

    # 1. Every id-bearing route, all ids from house 2, called by house 1's people.
    foreign = World(home=h2, who=h1.who, other_house_id=h1.house_id, storage=storage)
    for row in _path_rows(served):
        for role in CALLERS:
            label = f"{row.method} {row.path} as house-1 {role}"
            try:
                resp = await call(row, foreign, role)
            except Exception as exc:  # the in-process transport re-raises server errors
                problems.append(f"{label}: server error {exc!r}")
                continue
            if resp.status_code not in (403, 404):
                problems.append(f"{label}: {resp.status_code} {resp.text[:160]}")
            if leaked := _leaks(resp, h2):
                problems.append(f"{label}: response shows house 2's {leaked}")

    # 2. House-1 routes with house-2 ids in the body or as the second path id.
    probes = _mixed_probes(h1, h2)
    versions = [await _if_match_for(h1, method, url) for _, method, url, _ in probes]  # before anything changes
    for (caller, method, url, body), version in zip(probes, versions, strict=True):
        label = f"{method} {url} {json.dumps(body)} as house-1 {caller}"
        try:
            resp = await h1.who[caller].call(method, url, body, if_match=version, idem=True)
        except Exception as exc:
            problems.append(f"{label}: server error {exc!r}")
            continue
        if resp.status_code not in REFUSED and not _idempotent_no_op(method, url, resp):
            problems.append(f"{label}: {resp.status_code} {resp.text[:160]}")
        if leaked := _leaks(resp, h2):
            problems.append(f"{label}: response shows house 2's {leaked}")

    # 3. House 2 is untouched, and house 1 still works for its own people.
    after = await _snapshot(h2)
    if after != before:
        problems.append("house 2 changed after house 1's attempts")
    for url in (
        f"/api/v1/houses/{h1.house_id}/inventory?place=fridge",
        f"/api/v1/houses/{h1.house_id}/kitchen",
        f"/api/v1/bills/{h1.open_id}/money",
        f"/api/v1/houses/{h1.house_id}/balances",
    ):
        try:
            status = (await h1.who[ADMIN].get(url)).status_code
        except Exception as exc:
            problems.append(f"GET {url} as house-1 admin afterwards: server error {exc!r}")
            continue
        if status != 200:
            problems.append(f"GET {url} as house-1 admin afterwards: {status}")
    assert not problems, "\n".join(problems)


def _idempotent_no_op(method: str, url: str, resp: httpx.Response) -> bool:
    """Deleting an item that isn't on this tab is the set-state no-op (`already gone`, PRD §21.3):
    200 with the id it was given and no event. The snapshot proves nothing in house 2 moved."""
    if method != "DELETE" or "/items/" not in url or resp.status_code != 200:
        return False
    return bool(resp.json() == {"id": url.rsplit("/", 1)[1], "deleted": True})
