"""A house with every role and every kind of resource, for the security suites (TEST_PLAN 3.10).

Roles (SEC-01): `admin` (created the house), `payer` (claimed roommate who paid both tabs),
`member` (claimed roommate), `guest` (signed in, holds only the bill link), `outsider` (signed in,
admin of a different house) and `anon` (signed out). Kabir stays an unclaimed placeholder.

Resources: a draft and an open tab paid by the payer, a pending payment by the member, a locked
leftovers item owned by the member with an ask from the admin, a push subscription of the member,
and (when the receipts routes exist) a receipt on the draft stored in an in-process fake Blob store.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.adapters.storage import FakeBlobStorage
from app.settings import REPO_ROOT
from fakeservices import fake_blob
from tests.harness.api import Api

ApiFactory = Callable[[], Api]

ADMIN, PAYER, MEMBER, GUEST, OUTSIDER, ANON = "admin", "payer", "member", "guest", "outsider", "anon"
ROLES = (ADMIN, PAYER, MEMBER, GUEST, OUTSIDER, ANON)
SIGNED_IN = frozenset({ADMIN, PAYER, MEMBER, GUEST, OUTSIDER})
HOUSE = frozenset({ADMIN, PAYER, MEMBER})
EVERYONE = frozenset(ROLES)

BLOB_ORIGIN = "https://blob.test"  # a different origin from WEB_ORIGIN (https://app.test)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 200


def blob_storage() -> FakeBlobStorage:
    """The fake Vercel Blob service in-process (no port), through the API's real Blob adapter."""
    transport = httpx.ASGITransport(app=fake_blob.create_app(BLOB_ORIGIN))
    return FakeBlobStorage(BLOB_ORIGIN, transport=transport)


def ok(r: httpx.Response, *codes: int) -> Any:
    assert r.status_code in (codes or (200, 201)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/") else None


@dataclass
class HouseWorld:
    tag: str
    who: dict[str, Api]
    emails: dict[str, str]
    house_id: str = ""
    house_name: str = ""
    invite_token: str = ""
    members: dict[str, str] = field(default_factory=dict)  # lower-case display name -> member id
    draft_id: str = ""
    draft_items: list[str] = field(default_factory=list)
    open_id: str = ""
    open_items: list[str] = field(default_factory=list)
    link_token: str = ""
    guest_id: str = ""
    payment_id: str = ""
    inv_item_id: str = ""
    ask_id: str = ""
    sub_id: str = ""
    sub_endpoint: str = ""
    admin_identity_id: str = ""
    receipt_id: str = ""
    receipt_key: str = ""
    spare_receipt_key: str = ""  # uploaded but not attached (a valid attach body)
    extraction_id: str = ""  # a scan offered on the draft ("Use the scanned items instead?")
    names: list[str] = field(default_factory=list)  # free text that must never leak to another house

    @property
    def admin_member(self) -> str:
        return self.members["pranav"]

    @property
    def payer_member(self) -> str:
        return self.members["arjun"]

    @property
    def member_member(self) -> str:
        return self.members["meera"]

    @property
    def placeholder_member(self) -> str:
        return self.members["kabir"]

    def key(self, member_id: str) -> str:
        return f"m:{member_id}"

    # ---- current versions (guarded edits send If-Match) -----------------------------------------

    async def house_version(self) -> int:
        return int(ok(await self.who[ADMIN].get(f"/api/v1/houses/{self.house_id}"))["house"]["version"])

    async def member_version(self, member_id: str) -> int:
        view = ok(await self.who[ADMIN].get(f"/api/v1/houses/{self.house_id}"))
        return int(next(m["version"] for m in view["members"] if m["id"] == member_id))

    async def bill_version(self, bill_id: str) -> int:
        return int(ok(await self.who[PAYER].get(f"/api/v1/bills/{bill_id}"))["bill"]["version"])

    async def item_version(self, bill_id: str, item_id: str) -> int:
        view = ok(await self.who[PAYER].get(f"/api/v1/bills/{bill_id}"))
        return int(next(i["version"] for i in view["items"] if i["id"] == item_id))

    async def inventory_version(self, item_id: str) -> int:
        for place in ("fridge", "pantry"):
            view = ok(await self.who[MEMBER].get(f"/api/v1/houses/{self.house_id}/inventory", params={"place": place}))
            for area in view["areas"]:
                for item in area["items"]:
                    if item["id"] == item_id:
                        return int(item["version"])
        raise AssertionError(f"inventory item {item_id} not found")


async def _sign_in(api: ApiFactory, email: str) -> Api:
    who = api()
    await who.sign_in(email)
    return who


async def build_house(
    api: ApiFactory,
    *,
    tag: str | None = None,
    receipts: bool = True,
    offers: bool = False,
    storage: FakeBlobStorage | None = None,
) -> HouseWorld:
    """One fully populated house. `receipts` builds a receipt through the real upload flow."""
    tag = tag or uuid.uuid4().hex[:8]
    emails = {role: f"{role}-{tag}@example.com" for role in (ADMIN, PAYER, MEMBER)}
    w = HouseWorld(tag, {}, emails)
    signed_in = await asyncio.gather(*(_sign_in(api, emails[role]) for role in (ADMIN, PAYER, MEMBER)))
    w.who = dict(zip((ADMIN, PAYER, MEMBER), signed_in, strict=True))
    w.house_name = f"Apt {tag}"
    created = ok(
        await w.who[ADMIN].post("/api/v1/houses", {"name": w.house_name, "roommates": ["Arjun", "Meera", "Kabir"]}),
        201,
    )
    w.house_id = created["house"]["id"]
    w.invite_token = created["invite_token"]
    w.members = {m["display_name"].lower(): m["id"] for m in created["members"]}
    w.members["pranav"] = created["member_id"]  # the admin's name comes from their email
    for role, name in ((PAYER, "arjun"), (MEMBER, "meera")):
        ok(await w.who[role].post(f"/api/v1/join/{w.invite_token}/claim", {"member_id": w.members[name]}))
    payer, member, admin = w.who[PAYER], w.who[MEMBER], w.who[ADMIN]

    # A draft the payer is building (so the admin is neither its creator nor its payer).
    w.draft_id = ok(await payer.post(f"/api/v1/houses/{w.house_id}/bills", {"store_name": f"Draft store {tag}"}), 201)[
        "id"
    ]
    for name, cents in ((f"Draft apples {tag}", 300), (f"Draft bread {tag}", 450)):
        w.draft_items.append(
            ok(await payer.post(f"/api/v1/bills/{w.draft_id}/items", {"name": name, "line_total_cents": cents}), 201)[
                "id"
            ]
        )
    w.guest_id = ok(await payer.post(f"/api/v1/bills/{w.draft_id}/guests", {"name": f"Guest{tag[:4]}"}), 201)["id"]

    # An open tab: two food lines everyone shares, sent by the payer.
    w.open_id = ok(
        await payer.post(
            f"/api/v1/houses/{w.house_id}/bills", {"store_name": f"Open store {tag}", "receipt_total_cents": 1000}
        ),
        201,
    )["id"]
    for name, cents, storage_tag in ((f"Open milk {tag}", 400, "fridge"), (f"Open rice {tag}", 600, "pantry")):
        w.open_items.append(
            ok(
                await payer.post(
                    f"/api/v1/bills/{w.open_id}/items",
                    {"name": name, "line_total_cents": cents, "storage": storage_tag},
                ),
                201,
            )["id"]
        )
    changes = [
        {"item_id": item, "participant": w.key(m), "on": True}
        for item in w.open_items
        for m in (w.admin_member, w.payer_member, w.member_member)
    ]
    ok(await payer.call("PUT", f"/api/v1/bills/{w.open_id}/allocations", {"changes": changes}))
    w.link_token = ok(await payer.post(f"/api/v1/bills/{w.open_id}/send"))["link_token"]

    w.payment_id = ok(
        await member.post(f"/api/v1/bills/{w.open_id}/payments", {"amount_cents": 100, "method": "venmo"}), 201
    )["id"]
    w.inv_item_id = ok(
        await member.post(f"/api/v1/houses/{w.house_id}/inventory", {"name": f"Leftover curry {tag}"}), 201
    )["id"]
    w.ask_id = ok(await admin.post(f"/api/v1/inventory/{w.inv_item_id}/requests", {"amount": "a_bit"}), 201)["id"]
    w.sub_endpoint = f"https://push.test/send/{tag}-{uuid.uuid4().hex}"
    w.sub_id = ok(
        await member.post(
            "/api/v1/push/subscriptions",
            {"endpoint": w.sub_endpoint, "keys": {"p256dh": "B" * 87, "auth": "A" * 22}},
            idem=False,
        ),
        201,
    )["id"]
    w.admin_identity_id = ok(await admin.get("/api/v1/me/identities"))[0]["id"]
    if receipts and storage is not None:
        w.receipt_key, w.receipt_id = await upload_receipt(w, storage, attach=True)
        w.spare_receipt_key, _ = await upload_receipt(w, storage, attach=False)
        if offers:  # the draft already has typed items, so a finished scan is offered, not applied
            scan = ok(
                await payer.post(
                    f"/api/v1/bills/{w.draft_id}/ocr", {"receipt_id": w.receipt_id, "parts": ocr_parts("i01")}
                )
            )
            w.extraction_id = scan["result"]["extraction_id"]
    w.names = [
        w.house_name,
        f"Draft store {tag}",
        f"Draft apples {tag}",
        f"Open store {tag}",
        f"Open milk {tag}",
        f"Leftover curry {tag}",
    ]
    return w


def ocr_parts(fixture: str) -> list[dict[str, Any]]:
    """Browser OCR output for one of the synthetic receipts (ADR-0020)."""
    payload = json.loads((REPO_ROOT / "packages" / "fixtures" / "receipts" / f"{fixture}.ocr.json").read_text("utf-8"))
    if payload.get("multi_photo"):
        return [{"pages": [p]} for p in payload["pages"]]
    return [{"pages": payload["pages"]}]


async def upload_receipt(w: HouseWorld, storage: FakeBlobStorage, *, attach: bool) -> tuple[str, str]:
    """authorize -> PUT to the fake Blob store -> attach, all as the draft's payer."""
    payer = w.who[PAYER]
    ticket = ok(
        await payer.post(
            "/api/v1/uploads/authorize", {"bill_id": w.draft_id, "mime": "image/jpeg", "bytes": len(JPEG)}, idem=False
        )
    )
    async with httpx.AsyncClient(transport=storage.transport) as blob:
        put = await blob.request(ticket["method"], ticket["upload_url"], content=JPEG, headers=ticket["headers"])
    assert put.status_code == 200, put.text
    if not attach:
        return ticket["storage_key"], ""
    attached = ok(
        await payer.post(
            f"/api/v1/bills/{w.draft_id}/receipts",
            {"storage_key": ticket["storage_key"], "mime": "image/jpeg", "bytes": len(JPEG)},
        ),
        201,
    )
    return ticket["storage_key"], attached["receipt"]["id"]


@dataclass
class World:
    """House 1 with all six roles; `other` is the outsider's own house."""

    home: HouseWorld
    who: dict[str, Api]
    other_house_id: str
    storage: FakeBlobStorage | None


async def build_world(api: ApiFactory, app: Any, *, receipts: bool, offers: bool = False) -> World:
    storage = None
    if receipts:
        storage = blob_storage()
        app.state.storage = storage
    tag = uuid.uuid4().hex[:8]
    home, guest, outsider = await asyncio.gather(
        build_house(api, tag=tag, receipts=receipts, offers=offers, storage=storage),
        _sign_in(api, f"guest-{tag}@example.com"),
        _sign_in(api, f"outsider-{tag}@example.com"),
    )
    who = {**home.who, GUEST: guest, OUTSIDER: outsider}
    ok(await who[GUEST].get(f"/api/v1/b/{home.link_token}"))  # holds the link, nothing else
    other = ok(await who[OUTSIDER].post("/api/v1/houses", {"name": f"Elsewhere {home.tag}", "roommates": []}), 201)
    who[ANON] = api()
    return World(home, who, other["house"]["id"], storage)
