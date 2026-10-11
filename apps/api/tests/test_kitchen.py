"""Fridge and pantry (PRD §19; TEST_PLAN 3.6): stocking, ownership, sharing, use-soon, asks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import HouseEvent, LedgerEntry, ShareRequest
from tests.harness.api import Api
from tests.harness.race import sync_iterations
from tests.harness.tags import fr, tid
from tests.test_bills import World, draft_with_items, tick_like_fixture
from tests.test_sync_api import codes, open_bill, race, tick

ApiFactory = Callable[[], Api]


async def fridge(who: Api, w: World, place: str = "fridge", filter_: str = "all") -> dict[str, Any]:
    r = await who.get(f"/api/v1/houses/{w.hid}/inventory", params={"place": place, "filter": filter_})
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def find(view: dict[str, Any], prefix: str) -> dict[str, Any]:
    return next(i for a in view["areas"] for i in a["items"] if i["name"].startswith(prefix))


async def kitchen_world(api: ApiFactory) -> tuple[World, str, list[str], dict[str, Api]]:
    """Open ShopRite bill; Meera joined the coffee (shoprite_after_meera_joins_coffee)."""
    w, bill_id, ids, p = await open_bill(api)
    await tick(p["meera"], bill_id, ids[11], w.key("meera"), True)
    return w, bill_id, ids, p


@tid("KITCH-05")
@fr("FR-65")
async def test_KITCH_05_fridge_has_freezer_and_refrigerated_pantry_one_area(api: ApiFactory) -> None:
    w, _, _, _ = await kitchen_world(api)
    view = await fridge(w.admin, w)
    assert [a["area"] for a in view["areas"]] == ["fridge", "freezer"]
    assert len(view["areas"][0]["items"]) == 6
    pantry = await fridge(w.admin, w, "pantry")
    assert [a["area"] for a in pantry["areas"]] == ["pantry"]
    assert {i["short_name"] for i in pantry["areas"][0]["items"]} == {
        "Basmati rice",
        "Bananas",
        "Protein bars",
        "Coffee beans",
    }


@tid("KITCH-04", "EDGE-19-06")
@fr("FR-62")
async def test_KITCH_04_deleting_or_retagging_removes_settling_changes_nothing(api: ApiFactory) -> None:
    w, bill_id, ids, _ = await kitchen_world(api)
    await w.admin.call("DELETE", f"/api/v1/bills/{bill_id}/items/{ids[9]}")  # spinach
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    eggs = next(i for i in view["items"] if i["id"] == ids[1])
    await w.admin.call(
        "PATCH", f"/api/v1/bills/{bill_id}/items/{ids[1]}", {"storage": "none"}, if_match=eggs["version"]
    )
    names = {i["name"] for a in (await fridge(w.admin, w))["areas"] for i in a["items"]}
    assert not any(n.startswith(("Baby spinach", "Eggs")) for n in names)
    before = await fridge(w.admin, w)
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    after = await fridge(w.admin, w)
    assert [i["id"] for a in after["areas"] for i in a["items"]] == [
        i["id"] for a in before["areas"] for i in a["items"]
    ]


@tid("KITCH-06")
@fr("FR-63", "FR-64")
async def test_KITCH_06_kitchen_panels_show_counts_and_only_use_soon(api: ApiFactory) -> None:
    w, _, _, _ = await kitchen_world(api)
    k = (await w.admin.get(f"/api/v1/houses/{w.hid}/kitchen")).json()
    assert k["panels"]["fridge"]["count"] == 6 and k["panels"]["pantry"]["count"] == 4
    await w.admin.post("/__test/clock", {"now": "2026-10-06T14:00:00-04:00"}, idem=False)  # 4 days after Oct 2
    k = (await w.admin.get(f"/api/v1/houses/{w.hid}/kitchen")).json()
    soon = [i["short_name"] for i in k["panels"]["fridge"]["use_soon"]]
    assert "Baby spinach" in soon and "Eggs" not in soon, "spinach 4/5 days, eggs 4/28"
    assert all(i["use_soon"] for i in k["panels"]["fridge"]["use_soon"])


@tid("KITCH-07")
@fr("FR-66")
async def test_KITCH_07_filter_counts_and_dimmed_matches(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    view = await fridge(p["meera"], w, filter_="mine")
    assert view["counts"]["all"] == 6 and view["counts"]["mine"] == 4  # milk, eggs, yogurt, spinach
    assert sum(i["matches"] for a in view["areas"] for i in a["items"]) == 4
    assert all("matches" in i for a in view["areas"] for i in a["items"]), "nothing disappears; non-matches dim"


@tid("KITCH-08", "EDGE-19-01")
@fr("FR-69")
async def test_KITCH_08_open_only_when_every_owner_says_yes(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    milk = find(await fridge(w.admin, w), "Whole milk")
    assert milk["is_open"] is False and milk["holdouts"] == ["Pranav", "Arjun", "Meera"], "in house order"
    assert [o["display_name"] for o in milk["owners"]] == ["Pranav", "Arjun", "Meera"]
    for who in (w.admin, p["arjun"]):
        assert (await who.call("PUT", f"/api/v1/inventory/{milk['id']}/sharing", {"open": True})).status_code == 200
    milk = find(await fridge(p["kabir"], w), "Whole milk")
    assert milk["is_open"] is False and milk["holdouts"] == ["Meera"]
    await p["meera"].call("PUT", f"/api/v1/inventory/{milk['id']}/sharing", {"open": True})
    assert find(await fridge(p["kabir"], w), "Whole milk")["open_for_me"] is True
    notmine = await p["kabir"].call("PUT", f"/api/v1/inventory/{milk['id']}/sharing", {"open": True})
    assert notmine.status_code == 403
    eggs = find(await fridge(w.admin, w), "Eggs")  # everyone owns the eggs: nobody can ask
    r = await p["kabir"].post(f"/api/v1/inventory/{eggs['id']}/requests", {"amount": "a_bit"})
    assert r.status_code == 409


@tid("KITCH-09")
@fr("FR-70")
async def test_KITCH_09_share_by_default_applies_to_new_items(api: ApiFactory) -> None:
    w, bill_id, _ids, p = await open_bill(api)
    await p["kabir"].call("PATCH", "/api/v1/me", {"share_by_default": True})

    b2, ids2 = await draft_with_items(w)
    await tick_like_fixture(w, b2, ids2)
    await w.admin.post(f"/api/v1/bills/{b2}/send")
    view = await fridge(w.admin, w)
    newer = [
        i for a in view["areas"] for i in a["items"] if i["source"]["bill_id"] == b2 and i["name"].startswith("Oat")
    ]
    older = [
        i
        for a in view["areas"]
        for i in a["items"]
        if i["source"]["bill_id"] == bill_id and i["name"].startswith("Oat")
    ]
    assert newer[0]["owners"][0]["open_to_share"] is True and older[0]["owners"][0]["open_to_share"] is False


@tid("KITCH-10", "KITCH-11")
@fr("FR-78")
async def test_KITCH_10_use_soon_still_there_still_good_never_removed(api: ApiFactory) -> None:
    w, _, _, _p = await kitchen_world(api)
    spinach_id = find(await fridge(w.admin, w), "Baby spinach")["id"]

    async def at(when: str) -> dict[str, Any]:
        await w.admin.post("/__test/clock", {"now": when}, idem=False)
        return find(await fridge(w.admin, w), "Baby spinach")

    # Stocked Oct 2 (house time, New York), 5-day shelf life: 80% on Oct 6, 100% on Oct 7.
    assert (await at("2026-10-06T03:00:00+00:00"))["use_soon"] is False, "still Oct 5 in New York"
    s = await at("2026-10-06T14:00:00+00:00")
    assert (s["use_soon"], s["probably_gone"], s["soon_label"]) == (True, False, "1 DAY LEFT")
    s = await at("2026-10-07T14:00:00+00:00")
    assert (s["probably_gone"], s["soon_label"]) == (True, "STILL THERE?")
    good = await w.admin.post(f"/api/v1/inventory/{spinach_id}/still-good")
    assert good.json()["snoozed_until"] == "2026-10-10"
    s = find(await fridge(w.admin, w), "Baby spinach")
    assert s["probably_gone"] is False and s["days_left"] == 3
    s = await at("2026-10-26T14:00:00+00:00")  # far past 300%
    assert s["status"] == "in_stock", "items are never removed automatically"


@tid("KITCH-12", "SYNC-13")
@fr("FR-77", "FR-80")
async def test_KITCH_12_used_up_undo_and_non_owners_cannot(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, p = await kitchen_world(api)
    milk = find(await fridge(w.admin, w), "Whole milk")
    assert (await p["kabir"].post(f"/api/v1/inventory/{milk['id']}/used-up")).status_code == 403
    out = await race(
        lambda: w.admin.post(f"/api/v1/inventory/{milk['id']}/used-up"),
        lambda: p["arjun"].post(f"/api/v1/inventory/{milk['id']}/used-up"),
    )
    assert codes(out) == [200, 200], "idempotent"
    async with sessions() as db:
        events = (
            await db.scalars(
                select(HouseEvent).where(HouseEvent.house_id == w.hid, HouseEvent.type == "inventory_changed")
            )
        ).all()
    assert len([e for e in events if (e.data or {}).get("status") == "used_up"]) == 1, "one event (SYNC-13)"
    assert not any(i["id"] == milk["id"] for a in (await fridge(w.admin, w))["areas"] for i in a["items"])
    assert (await p["meera"].post(f"/api/v1/inventory/{milk['id']}/restore")).status_code == 200
    assert any(i["id"] == milk["id"] for a in (await fridge(w.admin, w))["areas"] for i in a["items"])
    hist = (await w.admin.get(f"/api/v1/houses/{w.hid}/kitchen/history")).json()
    assert hist["used_up"] == []
    await w.admin.post(f"/api/v1/inventory/{milk['id']}/used-up")
    await w.admin.post("/__test/clock", {"advance_seconds": 61}, idem=False)
    assert (await w.admin.post(f"/api/v1/inventory/{milk['id']}/restore")).status_code == 409
    assert (
        (await w.admin.get(f"/api/v1/houses/{w.hid}/kitchen/history")).json()["used_up"][0]["name"].startswith("Whole")
    )


@tid("KITCH-13", "ASK-11", "EDGE-19-07")
@fr("FR-79")
async def test_KITCH_13_leftovers_only_free_answers(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    r = await p["meera"].post(f"/api/v1/houses/{w.hid}/inventory", {"name": "Dal leftovers", "place": "fridge"})
    assert r.status_code == 201
    ask = await p["kabir"].post(f"/api/v1/inventory/{r.json()['id']}/requests", {"amount": "half"})
    inbox = (await p["meera"].get(f"/api/v1/houses/{w.hid}/requests", params={"box": "in"})).json()["requests"]
    assert inbox[0]["options"] == ["free"]
    split = await p["meera"].post(f"/api/v1/requests/{ask.json()['id']}/answer", {"decision": "split"})
    assert split.status_code == 409
    free = await p["meera"].post(f"/api/v1/requests/{ask.json()['id']}/answer", {"decision": "free"})
    assert free.json()["resolution"] == "free"


@tid("KITCH-14", "EDGE-19-02", "ASK-12", "EDGE-19-09")
@fr("FR-06")
async def test_KITCH_14_owner_leaves_and_requester_leaves(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    bars_id = find(await fridge(w.admin, w, "pantry"), "Protein bars")["id"]  # Arjun only
    ask = await p["meera"].post(f"/api/v1/inventory/{bars_id}/requests", {"amount": "a_bit"})
    assert (await p["meera"].post(f"/api/v1/houses/{w.hid}/leave")).status_code == 200
    y = find(await fridge(p["kabir"], w), "Greek yogurt")
    assert y["owners"] == [] and y["is_open"] is True, "no owners left: open to everyone"
    reqs = (await p["arjun"].get(f"/api/v1/houses/{w.hid}/requests", params={"box": "in"})).json()["requests"]
    assert next(r for r in reqs if r["id"] == ask.json()["id"])["status"] == "cancelled"


@tid("ASK-01", "ASK-02", "ASK-03")
@fr("FR-71", "FR-72")
async def test_ASK_01_03_ask_then_on_the_house(api: ApiFactory, sessions: async_sessionmaker[AsyncSession]) -> None:
    from app.models import OutboxRow

    w, _, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    assert chicken["mine"] is False and chicken["open_for_me"] is False
    long_note = await p["kabir"].post(
        f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit", "note": "x" * 141}
    )
    assert long_note.status_code == 422
    r = await p["kabir"].post(
        f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit", "note": "For tonight's curry"}
    )
    assert r.status_code == 201 and r.json()["status"] == "pending"
    async with sessions() as db:
        kinds = (await db.scalars(select(OutboxRow.kind).where(OutboxRow.house_id == w.hid))).all()
    assert "asks" in kinds, "all owners are told through the outbox"
    own = await w.admin.post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
    assert own.status_code == 409, "owners can't ask for their own items"
    assert find(await fridge(p["kabir"], w), "Chicken thighs")["my_request"]["id"] == r.json()["id"]
    yes = await p["arjun"].post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "free"})
    assert yes.json()["resolution"] == "free" and yes.json()["answered_by"]["display_name"] == "Arjun"
    mine = find(await fridge(p["kabir"], w), "Chicken thighs")
    assert mine["open_for_me"] is True and mine["granted_to_me"] is True and mine["is_open"] is False
    assert find(await fridge(p["meera"], w), "Chicken thighs")["open_for_me"] is False, "for the asker only"


@tid("ASK-04", "MONEY-09")
@fr("FR-73")
async def test_ASK_04_add_to_split_matches_the_money_fixture(api: ApiFactory) -> None:
    w, bill_id, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    r = await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
    inbox = (await w.admin.get(f"/api/v1/houses/{w.hid}/requests", params={"box": "in"})).json()["requests"]
    preview = inbox[0]["split_deltas"]
    assert {k: v for k, v in preview.items()} == {w.key("kabir"): 383, w.key("pranav"): -192, w.key("arjun"): -191}
    yes = await w.admin.post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "split"})
    assert yes.json()["resolution"] == "split"
    bill = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    expected = {"pranav": 1990, "arjun": 3200, "meera": 2224, "kabir": 2104}  # request_add_to_split
    assert {n: bill["shares"][w.key(n)] for n in expected} == expected
    assert find(await fridge(p["kabir"], w), "Chicken thighs")["mine"] is True, "the asker is now an owner"
    log = (await w.admin.get(f"/api/v1/bills/{bill_id}/activity")).json()["events"]
    assert any(e["payload"].get("text") == "Kabir joined Chicken thighs, approved by Pranav" for e in log)


@tid("ASK-05", "EDGE-19-04")
@fr("FR-74")
async def test_ASK_05_settled_bill_chip_in(api: ApiFactory, sessions: async_sessionmaker[AsyncSession]) -> None:
    w, bill_id, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    r = await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "half"})
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    split = await w.admin.post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "split"})
    assert split.status_code == 409 and split.json()["options"] == ["free", "chip_in"]
    inbox = (await w.admin.get(f"/api/v1/houses/{w.hid}/requests", params={"box": "in"})).json()["requests"]
    assert inbox[0]["options"] == ["free", "chip_in"] and inbox[0]["chip_in_suggestion_cents"] == 379
    too_much = await w.admin.post(
        f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "chip_in", "chip_in_cents": 1138}
    )
    assert too_much.status_code == 422
    ok = await w.admin.post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "chip_in", "chip_in_cents": 379})
    assert ok.json()["chip_in_cents"] == 379
    async with sessions() as db:
        entries = (await db.scalars(select(LedgerEntry).where(LedgerEntry.source == "chip_in"))).all()
    assert sorted(e.amount_cents for e in entries) == [189, 190], "split between the two owners"
    assert {str(e.from_member_id) for e in entries} == {w.member["kabir"]}
    bal = (await p["kabir"].get(f"/api/v1/houses/{w.hid}/balances")).json()
    assert {x["display_name"]: x["carried_cents"] for x in bal["pairs"]}["Arjun"] < 0


@tid("ASK-06", "ASK-08")
@fr("FR-72", "FR-76")
async def test_ASK_06_08_decline_and_cancel(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    bars = find(await fridge(w.admin, w, "pantry"), "Protein bars")
    r = await w.admin.post(f"/api/v1/inventory/{bars['id']}/requests", {"amount": "half"})
    no = await p["arjun"].post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "decline"})
    assert no.json()["status"] == "declined" and no.json()["resolution"] is None
    out = (await w.admin.get(f"/api/v1/houses/{w.hid}/requests", params={"box": "out"})).json()["requests"]
    assert out[0]["status"] == "declined" and "reason" not in out[0], "no reason shown"
    yogurt = find(await fridge(w.admin, w), "Greek yogurt")
    r2 = await w.admin.post(f"/api/v1/inventory/{yogurt['id']}/requests", {"amount": "a_bit"})
    assert (await p["meera"].post(f"/api/v1/requests/{r2.json()['id']}/cancel")).status_code == 403
    assert (await w.admin.post(f"/api/v1/requests/{r2.json()['id']}/cancel")).json()["status"] == "cancelled"


@tid("ASK-07")
@fr("FR-76")
async def test_ASK_07_asks_expire_after_48_hours(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    yogurt = find(await fridge(w.admin, w), "Greek yogurt")
    r = await w.admin.post(f"/api/v1/inventory/{yogurt['id']}/requests", {"amount": "a_bit"})
    await w.admin.post("/__test/clock", {"advance_seconds": 48 * 3600 + 1}, idem=False)
    late = await p["meera"].post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "free"})
    assert late.status_code == 410
    out = (await w.admin.get(f"/api/v1/houses/{w.hid}/requests", params={"box": "out"})).json()["requests"]
    assert out[0]["status"] == "expired", "the asker is told"


@tid("ASK-09")
@fr("FR-76")
async def test_ASK_09_owner_undo_within_five_minutes_including_split(api: ApiFactory) -> None:
    w, bill_id, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    before = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["shares"]
    r = await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
    await w.admin.post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "split"})
    undo = await w.admin.post(f"/api/v1/requests/{r.json()['id']}/undo")
    assert undo.json()["status"] == "pending"
    assert (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["shares"] == before, "allocation removed again"
    await w.admin.post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "free"})
    await w.admin.post("/__test/clock", {"advance_seconds": 301}, idem=False)
    assert (await w.admin.post(f"/api/v1/requests/{r.json()['id']}/undo")).status_code == 409


@tid("ASK-10")
@fr("FR-71")
async def test_ASK_10_limits(api: ApiFactory) -> None:
    w, _, _, _p = await kitchen_world(api)
    yogurt = find(await fridge(w.admin, w), "Greek yogurt")
    url = f"/api/v1/inventory/{yogurt['id']}/requests"
    first = await w.admin.post(url, {"amount": "a_bit"})
    assert (await w.admin.post(url, {"amount": "half"})).status_code == 409, "one pending per item"
    for _ in range(2):
        await w.admin.post(f"/api/v1/requests/{first.json()['id']}/cancel")
        first = await w.admin.post(url, {"amount": "a_bit"})
    await w.admin.post(f"/api/v1/requests/{first.json()['id']}/cancel")
    fourth = await w.admin.post(url, {"amount": "a_bit"})
    assert fourth.status_code == 429, "3 asks per item per 7 days"


@tid("ASK-13")
@fr("FR-75")
async def test_ASK_13_inbox_tabs_counts_and_kitchen_badge(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    for prefix, who in (("Chicken thighs", p["kabir"]), ("Baby spinach", p["kabir"])):
        item = find(await fridge(who, w), prefix)
        assert (await who.post(f"/api/v1/inventory/{item['id']}/requests", {"amount": "a_bit"})).status_code == 201
    inbox = (await w.admin.get(f"/api/v1/houses/{w.hid}/requests", params={"box": "in"})).json()
    assert inbox["counts"] == {"in": 2, "out": 0}
    assert (await w.admin.get(f"/api/v1/houses/{w.hid}/kitchen")).json()["asks_for_me"] == 2
    assert (await p["kabir"].get(f"/api/v1/houses/{w.hid}/requests", params={"box": "out"})).json()["counts"][
        "out"
    ] == 2


@pytest.mark.sync
@tid("SYNC-07", "EDGE-19-03")
@fr("FR-72")
async def test_SYNC_07_two_owners_answer_the_same_ask(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    owners = [w.member["pranav"], w.member["arjun"]]
    for i in range(sync_iterations()):
        made = await w.admin.post(
            f"/api/v1/houses/{w.hid}/inventory", {"name": f"Biryani {i}", "owner_member_ids": owners}
        )
        r = await p["kabir"].post(f"/api/v1/inventory/{made.json()['id']}/requests", {"amount": "a_bit"})
        rid = r.json()["id"]
        out = await race(
            lambda: w.admin.post(f"/api/v1/requests/{rid}/answer", {"decision": "free"}),
            lambda: p["arjun"].post(f"/api/v1/requests/{rid}/answer", {"decision": "decline"}),
        )
        assert codes(out) == [200, 409]
        winner = next(x for x in out if x.status_code == 200).json()
        loser = next(x for x in out if x.status_code == 409).json()
        assert loser["code"] == "ALREADY_ANSWERED"
        assert loser["title"] == f"Already answered by {winner['answered_by']['display_name']}"
        assert loser["request"]["status"] == winner["status"], "the loser sees how it was answered"
    # One free against one add-to-split on a bill line (the PRD row's example).
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    rid = (await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})).json()["id"]
    out = await race(
        lambda: w.admin.post(f"/api/v1/requests/{rid}/answer", {"decision": "split"}),
        lambda: p["arjun"].post(f"/api/v1/requests/{rid}/answer", {"decision": "free"}),
    )
    assert codes(out) == [200, 409]
    split_won = out[0].status_code == 200
    assert find(await fridge(p["kabir"], w), "Chicken thighs")["mine"] is split_won, "only the winner's answer applies"


@pytest.mark.sync
@tid("SYNC-08")
@fr("FR-73")
async def test_SYNC_08_add_to_split_while_the_payer_settles(api: ApiFactory) -> None:
    for _ in range(max(1, sync_iterations() // 10)):
        w, bill_id, _, p = await kitchen_world(api)
        chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
        rid = (await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})).json()["id"]
        out = await race(
            lambda: w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True}),
            lambda: p["arjun"].post(f"/api/v1/requests/{rid}/answer", {"decision": "split"}),
        )
        settle, answer = out
        assert settle.status_code == 200
        if answer.status_code == 409:
            assert answer.json()["code"] == "BILL_SETTLED" and answer.json()["options"] == ["free", "chip_in"]
        else:
            assert answer.status_code == 200  # approved first; the settle then carried the new shares


@tid("SYNC-14", "EDGE-19-05")
@fr("FR-77")
async def test_SYNC_14_used_up_while_an_ask_is_pending(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    r = await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
    await p["arjun"].post(f"/api/v1/inventory/{chicken['id']}/used-up")
    late = await w.admin.post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "free"})
    assert late.status_code == 410
    async with sessions() as db:
        assert (await db.get(ShareRequest, r.json()["id"])).status == "expired"  # type: ignore[union-attr]


@tid("EDGE-19-08")
@fr("FR-61")
async def test_EDGE_19_08_same_product_on_two_bills_two_tiles(api: ApiFactory) -> None:

    w, _, _, _ = await kitchen_world(api)
    b2, ids2 = await draft_with_items(w)
    await tick_like_fixture(w, b2, ids2)
    await w.admin.post(f"/api/v1/bills/{b2}/send")
    milks = [i for a in (await fridge(w.admin, w))["areas"] for i in a["items"] if i["name"].startswith("Whole milk")]
    assert len(milks) == 2 and len({m["source"]["bill_id"] for m in milks}) == 2


@tid("ASK-10")
@fr("FR-71")
async def test_ASK_10_at_most_ten_pending_outgoing(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    made = [
        (await p["meera"].post(f"/api/v1/houses/{w.hid}/inventory", {"name": f"Leftovers {n}"})).json()["id"]
        for n in range(11)
    ]
    for item_id in made[:10]:
        assert (await p["kabir"].post(f"/api/v1/inventory/{item_id}/requests", {"amount": "a_bit"})).status_code == 201
    eleventh = await p["kabir"].post(f"/api/v1/inventory/{made[10]}/requests", {"amount": "a_bit"})
    assert eleventh.status_code == 429


@tid("ASK-14")
@fr("FR-71", "FR-72", "FR-73", "FR-74", "FR-76")
async def test_ASK_14_every_arrow_of_the_request_diagram(api: ApiFactory) -> None:
    """PRD §19.4 diagram: each of the 10 arrows, in one walk over the chicken thighs."""
    w, bill_id, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")["id"]
    walked: list[str] = []

    async def ask() -> str:
        r = await p["kabir"].post(f"/api/v1/inventory/{chicken}/requests", {"amount": "a_bit"})
        assert r.json()["status"] == "pending"
        walked.append("[*]->Pending")
        rid: str = r.json()["id"]
        return rid

    async def step(who: Api, rid: str, path: str, body: dict[str, Any] | None, status: str, arrow: str) -> None:
        r = await who.post(f"/api/v1/requests/{rid}/{path}", body)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
        walked.append(arrow)

    rid = await ask()
    await step(w.admin, rid, "answer", {"decision": "free"}, "approved", "Pending->YesOnTheHouse")
    await step(w.admin, rid, "undo", None, "pending", "YesOnTheHouse->Pending")
    await step(p["arjun"], rid, "answer", {"decision": "split"}, "approved", "Pending->YesAddedToSplit")
    await step(p["arjun"], rid, "undo", None, "pending", "YesAddedToSplit->Pending")
    await step(w.admin, rid, "answer", {"decision": "decline"}, "declined", "Pending->NotThisTime")
    rid = await ask()
    await step(p["kabir"], rid, "cancel", None, "cancelled", "Pending->Cancelled")
    rid = await ask()
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    await step(w.admin, rid, "answer", {"decision": "chip_in"}, "approved", "Pending->YesChipIn")
    await step(w.admin, rid, "undo", None, "pending", "YesChipIn->Pending")
    await w.admin.post(f"/api/v1/inventory/{chicken}/used-up")
    out = (await p["kabir"].get(f"/api/v1/houses/{w.hid}/requests", params={"box": "out"})).json()["requests"]
    assert next(r for r in out if r["id"] == rid)["status"] == "expired"
    walked.append("Pending->Expired")
    assert sorted(set(walked)) == sorted(
        {
            "[*]->Pending", "Pending->YesOnTheHouse", "Pending->YesAddedToSplit", "Pending->YesChipIn",
            "Pending->NotThisTime", "Pending->Expired", "Pending->Cancelled", "YesOnTheHouse->Pending",
            "YesAddedToSplit->Pending", "YesChipIn->Pending",
        }
    )  # fmt: skip


@tid("ASK-13")
@fr("FR-75", "FR-72")
async def test_ASK_13_request_cards_carry_owners_source_and_line_total(api: ApiFactory) -> None:
    """K4 copy needs who owns it, the source tab and the chip-in ceiling; the item sheet names
    who approved a grant ("Help yourself: approved by Pranav", §19.3)."""
    w, _, _, p = await kitchen_world(api)
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    r = await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
    [card] = (await w.admin.get(f"/api/v1/houses/{w.hid}/requests", params={"box": "in"})).json()["requests"]
    assert card["owners"] == ["Pranav", "Arjun"] and card["co_owners"] == ["Arjun"]
    assert card["source"] == {"store_name": "ShopRite", "state": "open"} and card["line_total_cents"] == 1137
    [mine] = (await p["kabir"].get(f"/api/v1/houses/{w.hid}/requests", params={"box": "out"})).json()["requests"]
    assert mine["owners"] == ["Pranav", "Arjun"] and mine["source"]["store_name"] == "ShopRite"
    await w.admin.post(f"/api/v1/requests/{r.json()['id']}/answer", {"decision": "free"})
    granted = find(await fridge(p["kabir"], w), "Chicken thighs")
    assert granted["granted_to_me"] is True and granted["granted_by"] == "Pranav"
    assert find(await fridge(p["meera"], w), "Chicken thighs")["granted_by"] is None


@pytest.mark.sync
@tid("SYNC-13", "EDGE-19-01")
@fr("FR-62", "FR-69")
async def test_sharing_switch_races_an_untick_without_deadlock(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Review finding: a tick holds the house lock while syncing owners; a sharing switch holds an
    owner row and wants the house lock. Lock order (bill, lines, kitchen items, house) prevents it."""
    from app.services.invariants import check_all

    w, bill_id, ids, p = await kitchen_world(api)
    milk = find(await fridge(w.admin, w), "Whole milk")
    url = f"/api/v1/inventory/{milk['id']}/sharing"
    for _ in range(sync_iterations()):
        # Arjun owns the milk and isn't sharing; then the payer unticks him while he switches sharing on.
        assert (await tick(w.admin, bill_id, ids[0], w.key("arjun"), True)).status_code == 200
        assert (await p["arjun"].call("PUT", url, {"open": False})).status_code == 200
        untick, share = await race(
            lambda: tick(w.admin, bill_id, ids[0], w.key("arjun"), False),
            lambda: p["arjun"].call("PUT", url, {"open": True}),
        )
        assert untick.status_code == 200, untick.text
        assert share.status_code in (200, 403), share.text  # 403 if the untick committed first
    async with sessions() as db:
        assert await check_all(db, datetime.now(UTC)) == []
