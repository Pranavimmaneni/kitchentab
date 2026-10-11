"""Bills without OCR: manual entry, review, split, send, bill link (PRD §5.2-§5.4; TEST_PLAN 3.4)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ActivityEvent, InventoryItem, InventoryOwner, OutboxRow
from app.settings import REPO_ROOT
from tests.harness.api import Api
from tests.harness.tags import fr, tid

ApiFactory = Callable[[], Api]
SHOPRITE = json.loads((REPO_ROOT / "packages" / "fixtures" / "money_cases.json").read_text())[0]

# The mockup ShopRite receipt (Scan.dc.html) with storage tags.
LINES = [
    ("Whole milk 1 gal", 429, "fridge", "dairy", "milk"),
    ("Eggs 18 ct", 549, "fridge", "eggs", "eggs"),
    ("Basmati rice 10 lb", 1499, "pantry", "grains", "rice"),
    ("Greek yogurt 32 oz", 679, "fridge", "dairy", "yogurt"),
    ("Bananas 2.1 lb", 132, "pantry", "produce", "banana"),
    ("Paper towels 6 pk", 999, "none", "household", None),
    ("Dish soap", 349, "none", "household", None),
    ("Chicken thighs 3 lb", 1137, "fridge", "meat_fish", "chicken"),
    ("Oat milk", 459, "fridge", "drinks", "plant_milk"),
    ("Baby spinach", 399, "fridge", "produce", "leafy_greens"),
    ("Protein bars 12 ct", 1799, "pantry", "snacks", "bar"),
    ("Coffee beans 12 oz", 999, "pantry", "coffee_tea", "coffee"),
]
WHO = {  # from the money fixture `shoprite_sent`
    "milk": ["pranav", "arjun", "meera"], "eggs": ["pranav", "arjun", "meera", "kabir"],
    "rice": ["pranav", "arjun", "meera", "kabir"], "yogurt": ["meera"], "bananas": ["pranav", "kabir"],
    "towels": ["pranav", "arjun", "meera", "kabir"], "soap": ["pranav", "arjun", "meera", "kabir"],
    "chicken": ["pranav", "arjun"], "oatmilk": ["kabir"], "spinach": ["pranav", "meera"], "bars": ["arjun"],
    "coffee": ["pranav", "kabir"],
}  # fmt: skip
KEYS = [
    "milk",
    "eggs",
    "rice",
    "yogurt",
    "bananas",
    "towels",
    "soap",
    "chicken",
    "oatmilk",
    "spinach",
    "bars",
    "coffee",
]


class World:
    def __init__(self, admin: Api, house: dict[str, Any]) -> None:
        self.admin = admin
        self.house = house
        self.hid = house["house"]["id"]
        self.member = {m["display_name"].lower(): m["id"] for m in house["members"]}

    def key(self, name: str) -> str:
        return f"m:{self.member[name]}"


async def world(api: ApiFactory) -> World:
    admin = api()
    await admin.sign_in("pranav@example.com")
    return World(admin, await admin.create_house())


async def draft_with_items(w: World, receipt_total: int | None = 9518) -> tuple[str, list[str]]:
    bill = (
        await w.admin.post(
            f"/api/v1/houses/{w.hid}/bills",
            {"store_name": "ShopRite", "purchase_date": "2026-10-02", "receipt_total_cents": receipt_total},
        )
    ).json()
    ids = []
    for name, cents, storage, category, icon in LINES:
        r = await w.admin.post(
            f"/api/v1/bills/{bill['id']}/items",
            {"name": name, "line_total_cents": cents, "storage": storage, "category": category, "icon_key": icon},
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    view = (await w.admin.get(f"/api/v1/bills/{bill['id']}")).json()
    patched = await w.admin.call(
        "PATCH", f"/api/v1/bills/{bill['id']}", {"tax_cents": 89}, if_match=view["bill"]["version"]
    )
    assert patched.status_code == 200, patched.text
    return bill["id"], ids


async def tick_like_fixture(w: World, bill_id: str, item_ids: list[str]) -> None:
    changes = [
        {"item_id": iid, "participant": w.key(who), "on": True}
        for key, iid in zip(KEYS, item_ids, strict=True)
        for who in WHO[key]
    ]
    r = await w.admin.call("PUT", f"/api/v1/bills/{bill_id}/allocations", {"changes": changes})
    assert r.status_code == 200, r.text


@tid("BILL-01")
@fr("FR-14", "FR-15")
async def test_BILL_01_manual_bill_add_edit_delete_reorder(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert [i["name"] for i in view["items"]][:2] == ["Whole milk 1 gal", "Eggs 18 ct"]
    assert view["bill"]["subtotal_cents"] == 9429 and view["bill"]["total_cents"] == 9518
    milk = view["items"][0]
    edited = await w.admin.call(
        "PATCH",
        f"/api/v1/bills/{bill_id}/items/{milk['id']}",
        {"name": "Milk", "line_total_cents": 450},
        if_match=milk["version"],
    )
    assert edited.json()["name"] == "Milk" and edited.json()["version"] == 2
    stale = await w.admin.call("PATCH", f"/api/v1/bills/{bill_id}/items/{milk['id']}", {"name": "Other"}, if_match=1)
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    assert (await w.admin.call("DELETE", f"/api/v1/bills/{bill_id}/items/{ids[1]}")).status_code == 200
    order = list(reversed([i for i in ids if i != ids[1]]))
    assert (await w.admin.call("PUT", f"/api/v1/bills/{bill_id}/items/order", {"item_ids": order})).status_code == 200
    after = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert [i["id"] for i in after["items"]] == order
    assert after["bill"]["subtotal_cents"] == 9429 - 549 + 21


@tid("BILL-02", "EDGE-15-03")
@fr("FR-13")
async def test_BILL_02_reconciliation_blocks_until_fixed_or_overridden(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w, receipt_total=9638)  # receipt says $96.38, items say $95.18
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert view["reconciliation"] | {} == view["reconciliation"]
    assert view["reconciliation"]["difference_cents"] == 120 and view["reconciliation"]["ok"] is False
    await tick_like_fixture(w, bill_id, ids)
    blocked = await w.admin.post(f"/api/v1/bills/{bill_id}/send")
    assert blocked.status_code == 409 and "receipt total" in blocked.json()["title"]
    assert (await w.admin.post(f"/api/v1/bills/{bill_id}/reconcile-override", {"reason": "x"})).status_code == 422
    ok = await w.admin.post(f"/api/v1/bills/{bill_id}/reconcile-override", {"reason": "Bag fee not itemized"})
    assert ok.json()["total_cents"] == 9638 and ok.json()["adjustment_cents"] == 120
    sent = await w.admin.post(f"/api/v1/bills/{bill_id}/send")
    assert sent.status_code == 200
    by_name = {name: sent.json()["shares"][w.key(name)] for name in ("pranav", "arjun", "meera", "kabir")}
    assert by_name == {"pranav": 2380, "arjun": 3434, "meera": 1911, "kabir": 1913}, "matches reconciliation_adjustment"
    async with sessions() as db:
        logged = await db.scalar(select(ActivityEvent.payload).where(ActivityEvent.type == "reconcile_override"))
    assert logged == {"reason": "Bag fee not itemized", "difference_cents": 120}


@tid("BILL-03")
@fr("FR-60")
async def test_BILL_03_storage_tag_saves_per_item(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, _ = await draft_with_items(w)
    item = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["items"][0]
    version = item["version"]
    for tag in ("freezer", "pantry", "none", "fridge"):  # FRIDGE -> FREEZER -> PANTRY -> NOT FOOD -> FRIDGE
        r = await w.admin.call(
            "PATCH", f"/api/v1/bills/{bill_id}/items/{item['id']}", {"storage": tag}, if_match=version
        )
        assert r.json()["storage"] == tag
        version = r.json()["version"]
    household = await w.admin.call(
        "PATCH",
        f"/api/v1/bills/{bill_id}/items/{item['id']}",
        {"category": "household", "storage": "fridge"},
        if_match=version,
    )
    assert household.json()["storage"] == "none", "household always maps to NOT FOOD"


@tid("BILL-04", "EDGE-15-09")
@fr("FR-24")
async def test_BILL_04_send_blocked_while_any_item_has_no_one(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[0], "participant": w.key("pranav"), "on": True}]},
    )
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert view["progress"] == {"assigned": 1, "total": 12}
    r = await w.admin.post(f"/api/v1/bills/{bill_id}/send")
    assert r.status_code == 409 and r.json()["title"] == "11 items looking for a home"


@tid("BILL-05", "KITCH-01", "KITCH-02")
@fr("FR-30", "FR-31", "FR-61")
async def test_BILL_05_send_creates_link_shares_kitchen_and_one_notice(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    guest = (await w.admin.post(f"/api/v1/bills/{bill_id}/guests", {"name": "Sam"})).json()
    await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[1], "participant": guest["key"], "on": True}]},
    )
    key = "send-once"
    first = await w.admin.post(f"/api/v1/bills/{bill_id}/send", key=key)
    again = await w.admin.post(f"/api/v1/bills/{bill_id}/send", key=key)
    assert first.status_code == 200 and again.json() == first.json()
    assert len(first.json()["link_token"]) == 22
    assert sum(first.json()["shares"].values()) == 9518
    assert first.json()["stocked"] == 10, "12 lines minus 2 NOT FOOD"
    async with sessions() as db:
        inv = (await db.scalars(select(InventoryItem))).all()
        assert len(inv) == 10
        eggs = next(i for i in inv if i.name.startswith("Eggs"))
        owners = set(
            (
                await db.scalars(select(InventoryOwner.member_id).where(InventoryOwner.inventory_item_id == eggs.id))
            ).all()
        )
        assert {str(o) for o in owners} == {w.member[n] for n in WHO["eggs"]}, "guests never own kitchen items"
        assert await db.scalar(select(func.count()).select_from(OutboxRow).where(OutboxRow.kind == "tab")) == 1
    resend = await w.admin.post(f"/api/v1/bills/{bill_id}/send")
    assert resend.status_code == 409


@tid("BILL-07", "EDGE-15-08")
@fr("FR-17")
async def test_BILL_07_duplicate_warning(api: ApiFactory) -> None:
    w = await world(api)
    first, ids = await draft_with_items(w)
    await tick_like_fixture(w, first, ids)
    await w.admin.post(f"/api/v1/bills/{first}/send")
    second, _ = await draft_with_items(w)
    dup = (await w.admin.get(f"/api/v1/bills/{second}")).json()["duplicate_of"]
    assert dup == {"id": first, "store_name": "ShopRite", "purchase_date": "2026-10-02", "state": "open"}


@tid("SPLIT-01")
@fr("FR-20", "FR-23")
async def test_SPLIT_01_live_totals_match_compute_shares(api: ApiFactory) -> None:
    import time

    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    started = time.perf_counter()
    await tick_like_fixture(w, bill_id, ids)
    assert time.perf_counter() - started < 0.5, "a tick batch persists within 500 ms"
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    expected = SHOPRITE["expected"]["shares"]
    assert view["shares_final"] is True
    assert {n: view["shares"][w.key(n)] for n in expected} == expected


@tid("SPLIT-02")
@fr("FR-21")
async def test_SPLIT_02_split_the_rest_only_touches_unassigned(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[3], "participant": w.key("meera"), "on": True}]},
    )
    r = await w.admin.post(f"/api/v1/bills/{bill_id}/allocations/fill-rest")
    assert len(r.json()["filled"]) == 11
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    yogurt = next(i for i in view["items"] if i["id"] == ids[3])
    assert [a["participant"] for a in yogurt["allocations"]] == [w.key("meera")]
    assert all(len(i["allocations"]) == 4 for i in view["items"] if i["id"] != ids[3])


@tid("SPLIT-03")
@fr("FR-22")
async def test_SPLIT_03_column_toggle_sets_member_on_every_item(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    on = [{"item_id": i, "participant": w.key("kabir"), "on": True} for i in ids]
    await w.admin.call("PUT", f"/api/v1/bills/{bill_id}/allocations", {"changes": on})
    assert (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["progress"]["assigned"] == 12
    off = [{**c, "on": False} for c in on]
    r = await w.admin.call("PUT", f"/api/v1/bills/{bill_id}/allocations", {"changes": off})
    assert len(r.json()["applied"]) == 12
    assert (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["progress"]["assigned"] == 0


async def _claimed(api: ApiFactory, w: World, name: str) -> Api:
    who = api()
    await who.sign_in(f"{name}@example.com")
    r = await who.post(f"/api/v1/join/{w.house['invite_token']}/claim", {"member_id": w.member[name]})
    assert r.status_code == 200
    return who


@tid("SPLIT-06")
@fr("FR-32", "FR-33")
async def test_SPLIT_06_members_edit_only_their_own_column_payer_all(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    meera = await _claimed(api, w, "meera")
    draft_try = await meera.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[0], "participant": w.key("meera"), "on": True}]},
    )
    assert draft_try.status_code == 403, "drafts are the payer's"
    await tick_like_fixture(w, bill_id, ids)
    await w.admin.post(f"/api/v1/bills/{bill_id}/send")
    own = await meera.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[11], "participant": w.key("meera"), "on": True}]},
    )
    assert own.status_code == 200
    expected = {"pranav": 2182, "arjun": 3391, "meera": 2224, "kabir": 1721}  # shoprite_after_meera_joins_coffee
    assert {n: own.json()["shares"][w.key(n)] for n in expected} == expected
    other = await meera.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[0], "participant": w.key("arjun"), "on": False}]},
    )
    assert other.status_code == 403
    payer = await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[0], "participant": w.key("arjun"), "on": False}]},
    )
    assert payer.status_code == 200


@tid("SPLIT-07", "EDGE-15-10")
@fr("FR-34")
async def test_SPLIT_07_last_person_cannot_untick(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    await w.admin.post(f"/api/v1/bills/{bill_id}/send")
    meera = await _claimed(api, w, "meera")
    r = await meera.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[3], "participant": w.key("meera"), "on": False}]},
    )
    assert r.status_code == 409 and r.json()["code"] == "LAST_PERSON"
    assert "Ask the payer to remove the item" in r.json()["title"]


@tid("SPLIT-05")
@fr("FR-25")
async def test_SPLIT_05_guest_column_and_personal_link(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, _ = await draft_with_items(w)
    guest = (await w.admin.post(f"/api/v1/bills/{bill_id}/guests", {"name": "Sam"})).json()
    assert len(guest["link_token"]) == 22 and guest["key"].startswith("g:")
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    sam = next(p for p in view["participants"] if p["kind"] == "guest")
    assert sam["display_name"] == "Sam" and sam["sort_order"] == 4, "guests sort after members"
    house = (await w.admin.get(f"/api/v1/houses/{w.hid}")).json()
    assert "Sam" not in [m["display_name"] for m in house["members"]], "a guest is not a house member"


@tid("SPLIT-09")
@fr("FR-26")
async def test_SPLIT_09_exclude_member_from_one_bill(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[0], "participant": w.key("kabir"), "on": True}]},
    )
    r = await w.admin.call("PUT", f"/api/v1/bills/{bill_id}/exclusions/{w.member['kabir']}", {"excluded": True})
    assert r.status_code == 200
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert next(p for p in view["participants"] if p["display_name"] == "Kabir")["excluded"] is True
    assert view["items"][0]["allocations"] == [], "their ticks went with the column"
    filled = await w.admin.post(f"/api/v1/bills/{bill_id}/allocations/fill-rest")
    assert filled.status_code == 200
    after = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert all(w.key("kabir") not in [a["participant"] for a in i["allocations"]] for i in after["items"])


@tid("LINK-03", "EDGE-15-20")
@fr("FR-31")
async def test_LINK_03_bill_link_read_only_without_sign_in(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    token = (await w.admin.post(f"/api/v1/bills/{bill_id}/send")).json()["link_token"]
    anon = api()
    page = await anon.get(f"/api/v1/b/{token}")
    assert page.status_code == 200
    body = page.json()
    assert (
        body["bill"]["store_name"] == "ShopRite"
        and body["house"]["name"] == "Apt 4B"
        and body["payer_name"] == "Pranav"
    )
    assert "link_token" not in body["bill"]
    assert [p["display_name"] for p in body["join"]["placeholders"]] == ["Arjun", "Meera", "Kabir"]
    assert page.headers["X-Robots-Tag"] == "noindex" and page.headers["Referrer-Policy"] == "no-referrer"
    write = await anon.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[0], "participant": w.key("kabir"), "on": False}]},
    )
    assert write.status_code == 401
    await anon.sign_in("kabir@example.com")
    claimed = await anon.post(f"/api/v1/b/{token}/claim", {"member_id": w.member["kabir"]})
    assert claimed.status_code == 200
    assert (await anon.get(f"/api/v1/b/{token}")).json()["join"]["my_member"]["display_name"] == "Kabir"
    assert (await api().get("/api/v1/b/ABCDEFGHIJKLMNOPQRSTUV")).status_code == 410


@tid("KITCH-03")
@fr("FR-62")
async def test_KITCH_03_ticks_on_open_bill_change_owners_same_transaction(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    await w.admin.post(f"/api/v1/bills/{bill_id}/send")
    meera = await _claimed(api, w, "meera")
    await meera.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[11], "participant": w.key("meera"), "on": True}]},
    )
    await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[11], "participant": w.key("kabir"), "on": False}]},
    )
    async with sessions() as db:
        coffee = (await db.scalars(select(InventoryItem).where(InventoryItem.name.like("Coffee%")))).one()
        owners = {
            str(o)
            for o in (
                await db.scalars(select(InventoryOwner.member_id).where(InventoryOwner.inventory_item_id == coffee.id))
            ).all()
        }
    assert owners == {w.member["pranav"], w.member["meera"]}


@tid("EDGE-15-29")
async def test_EDGE_15_29_purchase_date_is_a_plain_date(api: ApiFactory) -> None:
    w = await world(api)
    bill = (
        await w.admin.post(f"/api/v1/houses/{w.hid}/bills", {"store_name": "Late", "purchase_date": "2026-10-02"})
    ).json()
    await w.admin.post("/__test/clock", {"now": "2026-10-03T03:30:00+00:00"}, idem=False)  # 11:30 PM in New York
    assert (await w.admin.get(f"/api/v1/bills/{bill['id']}")).json()["bill"]["purchase_date"] == "2026-10-02"
    today = (await w.admin.post(f"/api/v1/houses/{w.hid}/bills", {"store_name": "Default"})).json()
    assert (await w.admin.get(f"/api/v1/bills/{today['id']}")).json()["bill"]["purchase_date"] == "2026-10-02", (
        "defaults to today in the house's time zone, not UTC"
    )


async def test_merge_and_split_items(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    merged = await w.admin.post(f"/api/v1/bills/{bill_id}/items/merge", {"item_ids": ids[:2], "name": "Milk and eggs"})
    assert merged.json()["line_total_cents"] == 978
    split = await w.admin.post(
        f"/api/v1/bills/{bill_id}/items/{merged.json()['id']}/split",
        {"parts": [{"name": "Milk", "line_total_cents": 429}, {"name": "Eggs", "line_total_cents": 549}]},
    )
    assert split.status_code == 201
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert [i["name"] for i in view["items"]][:2] == ["Milk", "Eggs"] and view["bill"]["subtotal_cents"] == 9429
    bad = await w.admin.post(
        f"/api/v1/bills/{bill_id}/items/{view['items'][0]['id']}/split",
        {"parts": [{"line_total_cents": 1}, {"line_total_cents": 1}]},
    )
    assert bad.status_code == 422


async def test_delete_draft_and_policy(api: ApiFactory) -> None:
    w = await world(api)
    bill_id, _ = await draft_with_items(w)
    meera = await _claimed(api, w, "meera")
    assert (await meera.call("DELETE", f"/api/v1/bills/{bill_id}", idem=True)).status_code == 403
    assert (await w.admin.call("DELETE", f"/api/v1/bills/{bill_id}", idem=True)).status_code == 200
    assert (await w.admin.get(f"/api/v1/bills/{bill_id}")).status_code == 404


@tid("EXTRACT-05")
@fr("FR-12", "FR-15")
async def test_EXTRACT_05_fixing_a_flagged_line_clears_check_retagging_does_not(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A CHECK? row is cleared once the payer edits its name, quantity or price (they checked it);
    tapping its storage tag isn't checking the line, so the stamp stays."""
    from app.models import Item as ItemRow

    w = await world(api)
    bill_id, ids = await draft_with_items(w)
    async with sessions() as db, db.begin():
        for iid in ids[:2]:
            (await db.get(ItemRow, iid)).flagged = True  # type: ignore[union-attr]
    items = {i["id"]: i for i in (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["items"]}
    url = f"/api/v1/bills/{bill_id}/items"
    retag = await w.admin.call("PATCH", f"{url}/{ids[0]}", {"storage": "freezer"}, if_match=items[ids[0]]["version"])
    assert retag.status_code == 200 and retag.json()["flagged"] is True, "a tag tap leaves CHECK?"
    fixed = await w.admin.call("PATCH", f"{url}/{ids[1]}", {"name": "Eggs 18 ct"}, if_match=items[ids[1]]["version"])
    assert fixed.status_code == 200 and fixed.json()["flagged"] is False, "the payer checked it"
