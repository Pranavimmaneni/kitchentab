"""Open-bill life: payments, settle, reopen, void, reminders, change badges, activity (PRD §5.4, §6)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import InventoryItem, LedgerEntry
from tests.harness.api import Api
from tests.harness.tags import fr, tid
from tests.test_sync_api import open_bill, tick

ApiFactory = Callable[[], Api]


async def owed(w_admin: Api, bill_id: str) -> dict[str, int]:
    return dict((await w_admin.get(f"/api/v1/bills/{bill_id}/money")).json()["owed"])


@tid("PAY-01")
@fr("FR-36", "FR-37")
async def test_PAY_01_record_then_confirm_drops_owed(api: ApiFactory) -> None:
    w, bill_id, _, p = await open_bill(api)
    assert (await owed(w.admin, bill_id))[w.key("arjun")] == 3391
    rec = await p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", {"method": "venmo"})
    assert rec.json() | {} == rec.json()
    assert rec.json()["status"] == "pending_confirmation" and rec.json()["amount_cents"] == 3391
    assert (await owed(w.admin, bill_id))[w.key("arjun")] == 3391, "pending doesn't reduce owed"
    conf = await w.admin.post(f"/api/v1/payments/{rec.json()['id']}/confirm")
    assert conf.json()["status"] == "confirmed"
    assert (await owed(w.admin, bill_id))[w.key("arjun")] == 0
    again = await w.admin.post(f"/api/v1/payments/{rec.json()['id']}/confirm")
    assert again.status_code == 409 and again.json()["code"] == "ALREADY_ANSWERED"
    other = await p["meera"].post(f"/api/v1/payments/{rec.json()['id']}/confirm")
    assert other.status_code == 403


@tid("PAY-02", "EDGE-15-16")
@fr("FR-37")
async def test_PAY_02_reject_keeps_owed_and_notifies(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from app.models import OutboxRow

    w, bill_id, _, p = await open_bill(api)
    rec = await p["meera"].post(f"/api/v1/bills/{bill_id}/payments", {"method": "zelle"})
    r = await w.admin.post(f"/api/v1/payments/{rec.json()['id']}/reject", {"note": "Didn't arrive"})
    assert r.json()["status"] == "rejected" and r.json()["reject_note"] == "Didn't arrive"
    assert (await owed(w.admin, bill_id))[w.key("meera")] == 1887
    async with sessions() as db:
        kinds = (await db.scalars(select(OutboxRow.kind).order_by(OutboxRow.created_at))).all()
    assert kinds[-2:] == ["pay", "pay"], "payer told on record, member told on reject"


@tid("PAY-03")
@fr("FR-38")
async def test_PAY_03_payer_records_cash_confirmed_immediately(api: ApiFactory) -> None:
    w, bill_id, _, _ = await open_bill(api)
    r = await w.admin.post(f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("kabir"), "method": "cash"})
    assert r.json()["status"] == "confirmed" and r.json()["amount_cents"] == 1889
    assert (await owed(w.admin, bill_id))[w.key("kabir")] == 0


@tid("PAY-04", "PAY-05")
@fr("FR-36")
async def test_PAY_04_PAY_05_money_lists_the_payers_handles_for_the_pay_sheet(api: ApiFactory) -> None:
    w, bill_id, _, p = await open_bill(api)
    empty = (await p["arjun"].get(f"/api/v1/bills/{bill_id}/money")).json()
    assert empty["payer_handles"] == {"venmo": None, "cashapp": None, "paypal": None, "zelle": None}
    r = await w.admin.call("PUT", "/api/v1/me/payment-handles", {"venmo": "@pranav-r", "zelle": "p@example.com"})
    assert r.status_code == 200
    money = (await p["arjun"].get(f"/api/v1/bills/{bill_id}/money")).json()
    assert money["payer_handles"] == {"venmo": "pranav-r", "cashapp": None, "paypal": None, "zelle": "p@example.com"}
    # Only the payer's handles: the member's own handles never show on someone else's bill.
    await p["arjun"].call("PUT", "/api/v1/me/payment-handles", {"cashapp": "$arjun"})
    again = (await p["meera"].get(f"/api/v1/bills/{bill_id}/money")).json()
    assert again["payer_handles"]["cashapp"] is None


@tid("PAY-07")
async def test_PAY_07_one_reminder_per_member_per_bill_per_48h(api: ApiFactory) -> None:
    w, bill_id, _, _ = await open_bill(api)
    url = f"/api/v1/bills/{bill_id}/remind"
    assert (await w.admin.post(url, {"member_id": w.member["arjun"]})).status_code == 200
    second = await w.admin.post(url, {"member_id": w.member["arjun"]})
    assert second.status_code == 429 and int(second.headers["Retry-After"]) > 0
    assert (await w.admin.post(url, {"member_id": w.member["meera"]})).status_code == 200, "per member"
    await w.admin.post("/__test/clock", {"advance_seconds": 48 * 3600 + 1}, idem=False)
    assert (await w.admin.post(url, {"member_id": w.member["arjun"]})).status_code == 200


@tid("MONEY-07", "EDGE-15-14")
@fr("FR-36", "FR-39")
async def test_MONEY_07_overpay_becomes_credit_carried_on_settle(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Bill.dc.html: Kabir paid $18.89, then Meera joined the coffee: Kabir is owed $1.68 back."""
    w, bill_id, ids, p = await open_bill(api)
    await w.admin.post(f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("kabir"), "amount_cents": 1889})
    await tick(p["meera"], bill_id, ids[11], w.key("meera"), True)
    o = await owed(w.admin, bill_id)
    assert o[w.key("kabir")] == -168 and o[w.key("meera")] == 2224 and o[w.key("arjun")] == 3391
    r = await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    assert r.json()["carried_over"] == 3
    async with sessions() as db:
        entries = {
            (str(e.from_member_id), str(e.to_member_id), e.amount_cents)
            for e in (await db.scalars(select(LedgerEntry))).all()
        }
    pranav = w.member["pranav"]
    assert entries == {
        (w.member["arjun"], pranav, 3391),
        (w.member["meera"], pranav, 2224),
        (pranav, w.member["kabir"], 168),
    }


@tid("BILL-09")
@fr("FR-39")
async def test_BILL_09_settle_with_outstanding_needs_the_confirm_dialog(api: ApiFactory) -> None:
    w, bill_id, _, p = await open_bill(api)
    blocked = await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {})
    assert blocked.status_code == 409
    names = {row["name"]: row["owed_cents"] for row in blocked.json()["outstanding"]}
    assert names == {"Arjun": 3391, "Meera": 1887, "Kabir": 1889}
    assert (await p["arjun"].post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})).status_code == 403
    ok = await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    assert ok.json()["state"] == "settled"


@tid("BILL-10", "EDGE-15-24")
@fr("FR-40")
async def test_BILL_10_reopen_within_30_days_reverses_carryover(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, _, _ = await open_bill(api)
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    r = await w.admin.post(f"/api/v1/bills/{bill_id}/reopen")
    assert r.json()["state"] == "open"
    async with sessions() as db:
        entries = (await db.scalars(select(LedgerEntry))).all()
    net: dict[tuple[str, str], int] = {}
    for e in entries:
        low, high = sorted((str(e.from_member_id), str(e.to_member_id)))
        sign = 1 if str(e.from_member_id) == low else -1
        net[(low, high)] = net.get((low, high), 0) + sign * e.amount_cents
    assert all(v == 0 for v in net.values()), "reopening reverses the carryover; nothing is deleted"
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    await w.admin.post("/__test/clock", {"advance_seconds": 25 * 86400}, idem=False)
    assert (await w.admin.get("/api/v1/me")).status_code == 200  # the payer keeps using the app
    await w.admin.post("/__test/clock", {"advance_seconds": 6 * 86400}, idem=False)
    late = await w.admin.post(f"/api/v1/bills/{bill_id}/reopen")
    assert late.status_code == 409 and "30 days" in late.json()["title"]


@tid("BILL-08")
@fr("FR-42")
async def test_BILL_08_void_drops_from_balances_keeps_audit(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, _, _ = await open_bill(api)
    await w.admin.post(f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("kabir"), "amount_cents": 1889})
    assert (await w.admin.post(f"/api/v1/bills/{bill_id}/void", {"reason": "x"})).status_code == 422
    r = await w.admin.post(f"/api/v1/bills/{bill_id}/void", {"reason": "Wrong receipt"})
    assert r.json()["state"] == "voided"
    async with sessions() as db:
        credit = (await db.scalars(select(LedgerEntry))).one()
        stocked = (await db.scalars(select(InventoryItem.status))).all()
    assert (credit.source, credit.amount_cents, str(credit.to_member_id)) == ("void_credit", 1889, w.member["kabir"])
    assert set(stocked) == {"removed"}
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert view["bill"]["void_reason"] == "Wrong receipt"
    log = (await w.admin.get(f"/api/v1/bills/{bill_id}/activity")).json()["events"]
    assert log[0]["type"] == "bill_voided"


@tid("BILL-11")
@fr("FR-43")
async def test_BILL_11_activity_entry_for_every_state_change(api: ApiFactory) -> None:
    w, bill_id, ids, p = await open_bill(api)
    await tick(p["meera"], bill_id, ids[11], w.key("meera"), True)
    pay = await p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", {"method": "venmo"})
    await w.admin.post(f"/api/v1/payments/{pay.json()['id']}/confirm")
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    await w.admin.post(f"/api/v1/bills/{bill_id}/reopen")
    page1 = (await w.admin.get(f"/api/v1/bills/{bill_id}/activity", params={"limit": 3})).json()
    page2 = (
        await w.admin.get(f"/api/v1/bills/{bill_id}/activity", params={"limit": 50, "cursor": page1["next_cursor"]})
    ).json()
    types = [e["type"] for e in page1["events"] + page2["events"]]
    assert types[:5] == ["bill_reopened", "bill_settled", "payment_confirmed", "payment_recorded", "allocation_changed"]
    assert "bill_sent" in types and "item_added" in types
    assert page1["events"][0]["actor_name"] == "Pranav"


@tid("SPLIT-08")
@fr("FR-35")
async def test_SPLIT_08_changed_badge_until_viewed(api: ApiFactory) -> None:
    w, bill_id, ids, p = await open_bill(api)
    first = (await p["arjun"].get(f"/api/v1/bills/{bill_id}/money")).json()
    assert first["changed"] is None
    await p["arjun"].call("PUT", f"/api/v1/bills/{bill_id}/seen", {"version": first["version"]})
    await tick(p["meera"], bill_id, ids[0], w.key("meera"), False)  # Meera leaves the milk: Arjun pays more
    badge = (await p["arjun"].get(f"/api/v1/bills/{bill_id}/money")).json()
    assert badge["changed"]["delta_cents"] > 0
    await p["arjun"].call("PUT", f"/api/v1/bills/{bill_id}/seen", {"version": badge["version"]})
    cleared = (await p["arjun"].get(f"/api/v1/bills/{bill_id}/money")).json()
    assert cleared["changed"] is None


@tid("EDGE-15-17")
@fr("FR-31")
async def test_EDGE_15_17_payer_only_consumer_nobody_owes(api: ApiFactory) -> None:
    w, bill_id, ids, _ = await open_bill(api)
    for iid in ids:
        await w.admin.call(
            "PUT",
            f"/api/v1/bills/{bill_id}/allocations",
            {"changes": [{"item_id": iid, "participant": w.key("pranav"), "on": True}]},
        )
        for name in ("arjun", "meera", "kabir"):
            await w.admin.call(
                "PUT",
                f"/api/v1/bills/{bill_id}/allocations",
                {"changes": [{"item_id": iid, "participant": w.key(name), "on": False}]},
            )
    assert all(v == 0 for v in (await owed(w.admin, bill_id)).values())
    ok = await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {})
    assert ok.status_code == 200, "nothing outstanding: no confirm dialog needed"


@tid("EDGE-15-12")
@fr("FR-03")
async def test_EDGE_15_12_member_added_after_send_appears_only_when_added(api: ApiFactory) -> None:
    w, bill_id, ids, _ = await open_bill(api)
    dev = (await w.admin.post(f"/api/v1/houses/{w.hid}/members", {"name": "Dev"})).json()
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert all(f"m:{dev['id']}" not in [a["participant"] for a in i["allocations"]] for i in view["items"])
    r = await w.admin.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": ids[2], "participant": f"m:{dev['id']}", "on": True}]},
    )
    assert r.status_code == 200 and r.json()["shares"][f"m:{dev['id']}"] > 0


@tid("EDGE-15-25")
@fr("FR-35")
async def test_EDGE_15_25_open_for_months_gentle_banner_no_auto_settle(api: ApiFactory) -> None:
    """After 60 days the payer sees a gentle banner; nobody else does, and nothing settles itself."""
    w, bill_id, _, p = await open_bill(api)
    for days, step in ((20, 20), (40, 20), (59, 19)):  # use the sessions along the way (sliding 30 days)
        await w.admin.post("/__test/clock", {"advance_seconds": step * 86400}, idem=False)
        view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
        assert view["long_open"] is False, f"day {days}"
        await p["arjun"].get(f"/api/v1/bills/{bill_id}")
    await w.admin.post("/__test/clock", {"advance_seconds": 3 * 86400}, idem=False)  # day 62
    payer = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    member = (await p["arjun"].get(f"/api/v1/bills/{bill_id}")).json()
    assert payer["long_open"] is True and member["long_open"] is False
    assert payer["bill"]["state"] == "open", "no auto-settle"
    paid = await p["arjun"].post(
        f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("arjun"), "amount_cents": 3391, "method": "cash"}
    )
    assert paid.status_code == 201, "still works months later"
    assert (await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})).status_code == 200
    assert (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["long_open"] is False
