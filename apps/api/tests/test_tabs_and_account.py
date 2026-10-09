"""Tabs, balances, search, CSV and account settings (PRD §5.5, §7, §13; TEST_PLAN 3.5, 3.2)."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.models import AuthIdentity, LedgerEntry, Member, PushSubscription, Session, User
from app.seed.scenario import load_scenario
from app.seed.writer import write_scenario
from tests.harness.api import Api
from tests.harness.tags import fr, tid
from tests.test_bills import draft_with_items, tick_like_fixture
from tests.test_sync_api import open_bill

ApiFactory = Callable[[], Api]
PEPPER = "test-pepper-0123456789abcdef0123"


async def apt4b(api: ApiFactory, sessions: async_sessionmaker[AsyncSession]) -> tuple[dict[str, uuid.UUID], Api]:
    async with sessions() as db, db.begin():
        ids = await write_scenario(db, load_scenario("apt4b"), PEPPER)
    pranav = api()
    await pranav.sign_in("pranav@example.test")  # links the seeded user by verified email
    return ids, pranav


@tid("BAL-01", "MONEY-08")
@fr("FR-50", "FR-51")
async def test_BAL_01_apt4b_nets_to_plus_40_37(api: ApiFactory, sessions: async_sessionmaker[AsyncSession]) -> None:
    ids, pranav = await apt4b(api, sessions)
    view = (await pranav.get(f"/api/v1/houses/{ids['house:apt4b']}/balances")).json()
    assert view["net_cents"] == 4037
    pairs = {p["display_name"]: p["net_cents"] for p in view["pairs"]}
    assert pairs == {"Arjun": 3391, "Meera": 814, "Kabir": -168}, "Main.dc.html: ARJUN → YOU, MEERA → YOU, YOU → KABIR"
    meera = next(p for p in view["pairs"] if p["display_name"] == "Meera")
    assert sorted(b["cents"] for b in meera["bills"]) == [-1410, 2224], "netted per pair across ShopRite and H Mart"


@tid("BAL-03")
@fr("FR-42", "FR-74")
async def test_BAL_03_voided_excluded_chip_ins_included(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, _, _ = await open_bill(api)
    before = (await w.admin.get(f"/api/v1/houses/{w.hid}/balances")).json()
    assert before["net_cents"] == 3391 + 1887 + 1889
    await w.admin.post(f"/api/v1/bills/{bill_id}/void", {"reason": "Duplicate tab"})
    assert (await w.admin.get(f"/api/v1/houses/{w.hid}/balances")).json()["net_cents"] == 0
    async with sessions() as db, db.begin():
        from app.models import InventoryItem, ShareRequest

        inv = (await db.scalars(select(InventoryItem))).first()
        assert inv is not None
        req = ShareRequest(
            id=uuid7(),
            house_id=uuid.UUID(w.hid),
            inventory_item_id=inv.id,
            requester_member_id=uuid.UUID(w.member["kabir"]),
            amount="half",
            status="approved",
            resolution="chip_in",
            chip_in_cents=300,
            expires_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        db.add(req)
        await db.flush()
        db.add(
            LedgerEntry(
                id=uuid7(),
                house_id=uuid.UUID(w.hid),
                from_member_id=uuid.UUID(w.member["kabir"]),
                to_member_id=uuid.UUID(w.member["pranav"]),
                amount_cents=300,
                source="chip_in",
                share_request_id=req.id,
                created_at=datetime.now(UTC),
            )
        )
    after = (await w.admin.get(f"/api/v1/houses/{w.hid}/balances")).json()
    assert after["net_cents"] == 300, "the voided tab adds nothing; the chip-in counts"


@tid("BAL-04")
@fr("FR-50")
async def test_BAL_04_all_square_when_net_is_zero(api: ApiFactory) -> None:
    a = api()
    await a.sign_in("solo@example.com")
    house = await a.create_house(roommates=["Sam"])
    view = (await a.get(f"/api/v1/houses/{house['house']['id']}/balances")).json()
    assert view == {"net_cents": 0, "owed_to_me_cents": 0, "i_owe_cents": 0, "pairs": []}


@tid("BILL-12")
@fr("FR-54")
async def test_BILL_12_csv_one_row_per_item_and_person(api: ApiFactory) -> None:
    w, bill_id, _, _ = await open_bill(api)
    r = await w.admin.get(f"/api/v1/bills/{bill_id}/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    item_rows = [x for x in rows if x["item"] != "TOTAL"]
    assert len(item_rows) == 3 + 4 + 4 + 1 + 2 + 4 + 4 + 2 + 1 + 2 + 1 + 2
    totals = {x["person"]: x["person_item_share"] for x in rows if x["item"] == "TOTAL"}
    assert totals == {"Pranav": "23.51", "Arjun": "33.91", "Meera": "18.87", "Kabir": "18.89"}


@fr("FR-52", "FR-53")
async def test_tabs_lists_and_search(api: ApiFactory) -> None:
    w, bill_id, _, p = await open_bill(api)
    tabs = (await p["arjun"].get(f"/api/v1/houses/{w.hid}/tabs")).json()
    [card] = tabs["open"]
    assert card["store_name"] == "ShopRite" and card["payer_name"] == "Pranav" and card["item_count"] == 12
    assert (card["paid_count"], card["debtor_count"], card["my_owed_cents"]) == (0, 3, 3391)
    hits = (await p["arjun"].get(f"/api/v1/houses/{w.hid}/search", params={"q": "spinach"})).json()
    assert [h["id"] for h in hits] == [bill_id]
    assert (await p["arjun"].get(f"/api/v1/houses/{w.hid}/search", params={"q": "shop"})).json()[0]["id"] == bill_id


@fr("FR-52")
async def test_settled_history_pages_of_twenty(api: ApiFactory) -> None:
    w, bill_id, _, _ = await open_bill(api)
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    for _ in range(20):
        b, ids = await draft_with_items(w)
        await tick_like_fixture(w, b, ids)
        await w.admin.post(f"/api/v1/bills/{b}/send")
        await w.admin.post(f"/api/v1/bills/{b}/settle", {"confirm_outstanding": True})
    first = (await w.admin.get(f"/api/v1/houses/{w.hid}/tabs")).json()
    assert len(first["history"]) == 20 and first["next_cursor"]
    second = (await w.admin.get(f"/api/v1/houses/{w.hid}/tabs", params={"cursor": first["next_cursor"]})).json()
    assert len(second["history"]) == 1 and second["next_cursor"] is None


@tid("AUTH-11")
@fr("FR-01")
async def test_AUTH_11_attaching_a_method_tied_to_another_user_is_refused(api: ApiFactory) -> None:
    other = api()
    await other.sign_in("taken@example.com")
    me = api()
    await me.sign_in("me@example.com")
    r = await me.post(
        "/api/v1/me/identities/start", {"channel": "email", "destination": "taken@example.com"}, idem=False
    )
    assert r.status_code == 409 and r.json()["title"] == "This is linked to another account"


@tid("AUTH-12")
async def test_AUTH_12_cannot_remove_the_last_sign_in_method(api: ApiFactory) -> None:
    me = api()
    await me.sign_in("one@example.com")
    [only] = (await me.get("/api/v1/me/identities")).json()
    r = await me.call("DELETE", f"/api/v1/me/identities/{only['id']}")
    assert r.status_code == 422 and r.json()["code"] == "LAST_SIGN_IN_METHOD"
    start = await me.post(
        "/api/v1/me/identities/start", {"channel": "phone", "destination": "+12015550123"}, idem=False
    )
    assert start.status_code == 200
    [sms] = (await me.get("/__test/sms", params={"to": "+12015550123"})).json()
    added = await me.post(
        "/api/v1/me/identities/verify",
        {"channel": "phone", "destination": "+12015550123", "code": sms["code"]},
        idem=False,
    )
    assert added.status_code == 201
    assert (await me.call("DELETE", f"/api/v1/me/identities/{only['id']}")).status_code == 200


@tid("AUTH-14", "SEC-13", "EDGE-15-22")
@fr("FR-06")
async def test_AUTH_14_delete_account_anonymizes_and_keeps_amounts(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, _ids, p = await open_bill(api)
    shares_before = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["shares"]
    kabir = p["kabir"]
    async with sessions() as db, db.begin():
        user_id = (await db.scalars(select(Member.user_id).where(Member.id == uuid.UUID(w.member["kabir"])))).one()
        db.add(
            PushSubscription(
                id=uuid7(),
                user_id=user_id,
                endpoint="https://push.test/x",
                p256dh="k",
                auth="a",
                device_label="This iPhone",
                created_at=datetime.now(UTC),
            )
        )
    assert (await kabir.call("DELETE", "/api/v1/me", {"confirm": "nope"})).status_code == 422
    r = await kabir.call("DELETE", "/api/v1/me", {"confirm": "DELETE"})
    assert r.status_code == 200
    assert (await kabir.get("/api/v1/me")).status_code == 401
    after = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert after["shares"] == shares_before, "everyone else's amounts are unchanged"
    names = {pp["key"]: pp["display_name"] for pp in after["participants"]}
    assert names[w.key("kabir")] == "Former roommate"
    async with sessions() as db:
        for model in (Session, AuthIdentity, PushSubscription):
            assert await db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) == 0
        user = await db.get(User, user_id)
        assert user is not None and user.email is None and user.deleted_at is not None
    # A second deleted roommate can also be "Former roommate" in the same house.
    assert (await p["meera"].call("DELETE", "/api/v1/me", {"confirm": "DELETE"})).status_code == 200
    blocked = await w.admin.call("DELETE", "/api/v1/me", {"confirm": "DELETE"})
    assert blocked.status_code == 409, "the admin hands over first while others are active"


@tid("DATA-04")
async def test_DATA_04_export_my_data(api: ApiFactory) -> None:
    _w, _, _, p = await open_bill(api)
    r = await p["arjun"].get("/api/v1/me/export")
    data = r.json()
    assert set(data) == {
        "user",
        "sign_in_methods",
        "payment_handles",
        "notification_prefs",
        "houses",
        "bills_paid_for",
        "payments",
        "activity",
    }
    assert data["houses"][0]["display_name"] == "Arjun"
    assert data["sign_in_methods"][0]["provider"] == "email"


async def test_payment_handles_set_state(api: ApiFactory) -> None:
    me = api()
    await me.sign_in("handles@example.com")
    r = await me.call("PUT", "/api/v1/me/payment-handles", {"venmo": "@pranav-r", "zelle": "p@example.com"})
    assert r.json() == {"venmo": "pranav-r", "cashapp": None, "paypal": None, "zelle": "p@example.com"}
    assert (await me.get("/api/v1/me/payment-handles")).json()["venmo"] == "pranav-r"
    bad = await me.call("PUT", "/api/v1/me/payment-handles", {"venmo": "has spaces"})
    assert bad.status_code == 422
