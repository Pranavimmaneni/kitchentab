"""PRD §21.7 race matrix at API level (TEST_PLAN 3.7): concurrent requests hit real Postgres behind
a barrier; every SYNC test repeats `sync_iterations()` times (200 under `make test-sync`). After each
run: the final state, the event log (gapless, one event per effective write) and Σ shares = total."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.models import HouseEvent, Payment
from tests.harness.api import Api
from tests.harness.race import run_concurrently, sync_iterations
from tests.harness.tags import fr, tid
from tests.test_bills import World, draft_with_items, tick_like_fixture

ApiFactory = Callable[[], Api]


async def open_bill(api: ApiFactory) -> tuple[World, str, list[str], dict[str, Api]]:
    """A sent ShopRite bill (shoprite_sent) with Arjun, Meera and Kabir claimed."""
    admin = api()
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    w = World(admin, await admin.create_house())
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    assert (await admin.post(f"/api/v1/bills/{bill_id}/send")).status_code == 200
    people = {}
    for name in ("arjun", "meera", "kabir"):
        who = api()
        await who.sign_in(f"{name}+{uuid7()}@example.com")
        r = await who.post(f"/api/v1/join/{w.house['invite_token']}/claim", {"member_id": w.member[name]})
        assert r.status_code == 200
        people[name] = who
    return w, bill_id, ids, people


async def tick(who: Api, bill_id: str, item_id: str, participant: str, on: bool) -> Any:
    return await who.call(
        "PUT",
        f"/api/v1/bills/{bill_id}/allocations",
        {"changes": [{"item_id": item_id, "participant": participant, "on": on}]},
    )


async def allocations(w: World, bill_id: str, item_id: str) -> list[str]:
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    item = next(i for i in view["items"] if i["id"] == item_id)
    return sorted(a["participant"] for a in item["allocations"])


async def assert_invariants(w: World, bill_id: str, sessions: async_sessionmaker[AsyncSession]) -> None:
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    if view["shares_final"]:
        assert sum(view["shares"].values()) == view["bill"]["total_cents"]
    async with sessions() as db:
        seqs = (
            await db.scalars(select(HouseEvent.seq).where(HouseEvent.house_id == w.hid).order_by(HouseEvent.seq))
        ).all()
    assert list(seqs) == list(range(1, len(seqs) + 1)), "gapless event sequence"


def codes(responses: list[Any]) -> list[int]:
    return sorted(r.status_code for r in responses)


async def _call(fns: list[Callable[[], Any]], i: int) -> Any:
    return await fns[i]()


async def race(*fns: Callable[[], Any]) -> list[Any]:
    """Run the calls together behind a barrier; returns their responses in call order."""
    outcomes = await run_concurrently(len(fns), functools.partial(_call, list(fns)))
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    return [o.value for o in outcomes]


@pytest.mark.sync
@tid("SYNC-01", "EDGE-15-11")
@fr("FR-32")
async def test_SYNC_01_two_members_tick_themselves_on_the_same_item(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, p = await open_bill(api)
    yogurt = ids[3]  # Meera only
    for _ in range(sync_iterations()):
        await tick(w.admin, bill_id, yogurt, w.key("arjun"), False)
        await tick(w.admin, bill_id, yogurt, w.key("kabir"), False)
        out = await race(
            lambda: tick(p["arjun"], bill_id, yogurt, w.key("arjun"), True),
            lambda: tick(p["kabir"], bill_id, yogurt, w.key("kabir"), True),
        )
        assert codes(out) == [200, 200]
        assert await allocations(w, bill_id, yogurt) == sorted([w.key("meera"), w.key("arjun"), w.key("kabir")])
    await assert_invariants(w, bill_id, sessions)


@pytest.mark.sync
@tid("SYNC-02")
@fr("FR-33")
async def test_SYNC_02_member_and_payer_untick_the_same_cell(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, p = await open_bill(api)
    rice = ids[2]
    for _ in range(sync_iterations()):
        await tick(w.admin, bill_id, rice, w.key("kabir"), True)
        async with sessions() as db:
            before = await db.scalar(select(func.count()).select_from(HouseEvent).where(HouseEvent.house_id == w.hid))
        out = await race(
            lambda: tick(p["kabir"], bill_id, rice, w.key("kabir"), False),
            lambda: tick(w.admin, bill_id, rice, w.key("kabir"), False),
        )
        assert codes(out) == [200, 200]
        applied = sorted(len(r.json()["applied"]) for r in out)
        assert applied == [0, 1], "the second write is a no-op"
        assert w.key("kabir") not in await allocations(w, bill_id, rice)
        async with sessions() as db:
            after = await db.scalar(select(func.count()).select_from(HouseEvent).where(HouseEvent.house_id == w.hid))
        assert after == (before or 0) + 1, "one event"
    await assert_invariants(w, bill_id, sessions)


@pytest.mark.sync
@tid("SYNC-03", "EDGE-15-10")
@fr("FR-34")
async def test_SYNC_03_last_two_untick_at_once(api: ApiFactory, sessions: async_sessionmaker[AsyncSession]) -> None:
    w, bill_id, ids, p = await open_bill(api)
    chicken = ids[7]  # Pranav + Arjun
    for _ in range(sync_iterations()):
        await tick(w.admin, bill_id, chicken, w.key("arjun"), True)
        await tick(w.admin, bill_id, chicken, w.key("pranav"), True)
        out = await race(
            lambda: tick(p["arjun"], bill_id, chicken, w.key("arjun"), False),
            lambda: tick(w.admin, bill_id, chicken, w.key("pranav"), False),
        )
        assert codes(out) == [200, 409]
        loser = next(r for r in out if r.status_code == 409)
        assert loser.json()["code"] == "LAST_PERSON"
        assert len(await allocations(w, bill_id, chicken)) == 1
    await assert_invariants(w, bill_id, sessions)


@pytest.mark.sync
@tid("SYNC-04")
@fr("FR-32", "FR-33")
async def test_SYNC_04_price_edit_while_a_member_ticks(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, p = await open_bill(api)
    coffee = ids[11]
    for n in range(sync_iterations()):
        await tick(w.admin, bill_id, coffee, w.key("meera"), False)
        item = next(i for i in (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["items"] if i["id"] == coffee)
        price = 999 + (n % 2)
        out = await race(
            lambda: w.admin.call(
                "PATCH",
                f"/api/v1/bills/{bill_id}/items/{coffee}",
                {"line_total_cents": price},
                if_match=item["version"],
            ),
            lambda: tick(p["meera"], bill_id, coffee, w.key("meera"), True),
        )
        assert codes(out) == [200, 200], "ticks don't bump the item version"
        view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
        assert next(i for i in view["items"] if i["id"] == coffee)["line_total_cents"] == price
        assert w.key("meera") in await allocations(w, bill_id, coffee)
        assert sum(view["shares"].values()) == view["bill"]["total_cents"]
    await assert_invariants(w, bill_id, sessions)


@pytest.mark.sync
@tid("SYNC-05")
@fr("FR-33")
async def test_SYNC_05_two_devices_edit_the_same_price(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, _ = await open_bill(api)
    milk = ids[0]
    phone = w.admin
    laptop = Api(phone.http)  # same user, same session (two devices behave the same at the API)
    for n in range(sync_iterations()):
        item = next(i for i in (await phone.get(f"/api/v1/bills/{bill_id}")).json()["items"] if i["id"] == milk)
        out = await race(
            lambda: phone.call(
                "PATCH",
                f"/api/v1/bills/{bill_id}/items/{milk}",
                {"line_total_cents": 500 + n},
                if_match=item["version"],
            ),
            lambda: laptop.call(
                "PATCH",
                f"/api/v1/bills/{bill_id}/items/{milk}",
                {"line_total_cents": 600 + n},
                if_match=item["version"],
            ),
        )
        assert codes(out) == [200, 409]
        conflict = next(r for r in out if r.status_code == 409)
        assert conflict.json()["code"] == "VERSION_CONFLICT"
        assert conflict.json()["current"]["line_total_cents"] in (500 + n, 600 + n)
    await assert_invariants(w, bill_id, sessions)


@pytest.mark.sync
@tid("SYNC-06")
@fr("FR-39")
async def test_SYNC_06_settle_while_a_tick_is_in_flight(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    for _ in range(max(1, sync_iterations() // 10)):  # each run needs a fresh open bill
        w, bill_id, ids, p = await open_bill(api)
        out = await race(
            lambda: w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True}),
            lambda: tick(p["meera"], bill_id, ids[11], w.key("meera"), True),
        )
        settle, tick_resp = out
        assert settle.status_code == 200
        if tick_resp.status_code == 409:
            assert tick_resp.json()["code"] == "BILL_SETTLED"
        else:
            assert tick_resp.status_code == 200  # committed before the settle
        state = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["bill"]["state"]
        assert state == "settled"
        late = await tick(p["meera"], bill_id, ids[0], w.key("meera"), False)
        assert late.status_code == 409 and late.json()["code"] == "BILL_SETTLED"
        await assert_invariants(w, bill_id, sessions)


@pytest.mark.sync
@tid("SYNC-09", "PAY-06", "EDGE-15-15")
@fr("FR-36")
async def test_SYNC_09_double_tap_payment_is_one_payment(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    _w, bill_id, _, p = await open_bill(api)
    for n in range(sync_iterations()):
        key = f"pay-{n}"
        body = {"amount_cents": 1, "method": "venmo"}
        out = await race(
            lambda: p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", body, key=key),
            lambda: p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", body, key=key),
        )
        assert codes(out) == [201, 201]
        a, b = (r.json()["id"] for r in out)
        assert a == b, "same key returns the first payment"
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Payment)) == sync_iterations()


@pytest.mark.sync
@tid("SYNC-10")
@fr("FR-36", "FR-37")
async def test_SYNC_10_two_devices_record_with_different_keys(api: ApiFactory) -> None:
    w, bill_id, _, p = await open_bill(api)
    out = await race(
        lambda: p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", {"method": "venmo"}),
        lambda: p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", {"method": "zelle"}),
    )
    assert codes(out) == [201, 201]
    bodies = [r.json() for r in out]
    assert {b["status"] for b in bodies} == {"pending_confirmation"}
    assert sum("warning" in b for b in bodies) == 1, "the second shows 'you already recorded…'"
    warned = next(b for b in bodies if "warning" in b)
    assert warned["warning"]["amount_cents"] == 3391
    second = warned["id"]
    rejected = await w.admin.post(f"/api/v1/payments/{second}/reject", {"note": "Duplicate"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"


@pytest.mark.sync
@tid("SYNC-12")
@fr("FR-06")
async def test_SYNC_12_removed_member_mid_edit_gets_not_a_member(api: ApiFactory) -> None:
    w, bill_id, ids, p = await open_bill(api)
    out = await race(
        lambda: w.admin.call("DELETE", f"/api/v1/houses/{w.hid}/members/{w.member['kabir']}"),
        lambda: tick(p["kabir"], bill_id, ids[11], w.key("kabir"), False),
    )
    assert out[0].status_code == 200
    after = await tick(p["kabir"], bill_id, ids[4], w.key("kabir"), False)
    assert after.status_code == 403 and after.json()["code"] == "NOT_A_MEMBER"


@pytest.mark.sync
@tid("SYNC-16", "CHAOS-01")
@fr("FR-36")
async def test_SYNC_16_retry_after_a_lost_response_returns_the_original(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    _w, bill_id, _, p = await open_bill(api)
    key = "lost-response"
    first = await p["meera"].post(f"/api/v1/bills/{bill_id}/payments", {"method": "cash"}, key=key)
    # The response "never arrived": the client retries the same request with the same key.
    retry = await p["meera"].post(f"/api/v1/bills/{bill_id}/payments", {"method": "cash"}, key=key)
    assert retry.json() == first.json() and retry.headers["Idempotent-Replayed"] == "true"
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Payment)) == 1


@pytest.mark.sync
@tid("SYNC-23")
@fr("FR-14")
async def test_SYNC_23_payer_deletes_an_item_a_member_is_ticking(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, p = await open_bill(api)
    spinach = ids[9]
    out = await race(
        lambda: w.admin.call("DELETE", f"/api/v1/bills/{bill_id}/items/{spinach}"),
        lambda: tick(p["kabir"], bill_id, spinach, w.key("kabir"), True),
    )
    statuses = codes(out)
    assert statuses in ([200, 200], [200, 404])
    late = await tick(p["kabir"], bill_id, spinach, w.key("kabir"), True)
    assert late.status_code == 404 and late.json()["code"] == "ITEM_GONE"
    await assert_invariants(w, bill_id, sessions)


@tid("SYNC-28")
async def test_SYNC_28_missing_if_match_428_missing_key_400(api: ApiFactory) -> None:
    w, bill_id, ids, _ = await open_bill(api)
    r = await w.admin.call("PATCH", f"/api/v1/bills/{bill_id}/items/{ids[0]}", {"name": "x"})
    assert r.status_code == 428 and r.json()["code"] == "IF_MATCH_REQUIRED"
    r = await w.admin.call("POST", f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("arjun")}, idem=False)
    assert r.status_code == 400 and r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


@tid("SYNC-29")
async def test_SYNC_29_same_key_different_body_422(api: ApiFactory) -> None:
    _w, bill_id, _, p = await open_bill(api)
    await p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", {"amount_cents": 100}, key="k")
    r = await p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", {"amount_cents": 200}, key="k")
    assert r.status_code == 422 and r.json()["code"] == "IDEMPOTENCY_MISMATCH"
