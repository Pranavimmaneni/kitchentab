"""Kitchen gestures API (docs/features/kitchen-gestures/SPEC.md §5; TEST_PLAN 3.13): moving items
between Freezer, Fridge and Pantry, and each member's own order."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.main import create_app
from app.models import HouseEvent
from app.settings import Settings
from tests.harness.api import BASE_URL, Api
from tests.harness.race import sync_iterations
from tests.harness.tags import fr, tid
from tests.test_bills import World, draft_with_items, tick_like_fixture
from tests.test_kitchen import ApiFactory, find, fridge, kitchen_world
from tests.test_sync_api import codes, race


async def move(who: Api, w: World, *moves: dict[str, Any], key: str | None = None) -> Any:
    return await who.post(f"/api/v1/houses/{w.hid}/items/move", {"moves": list(moves)}, key=key)


async def place_of(w: World, item_id: str) -> str:
    for place in ("fridge", "pantry"):
        for area in (await fridge(w.admin, w, place))["areas"]:
            for i in area["items"]:
                if i["id"] == item_id:
                    return str(i["place"])
    return "gone"


async def events(sessions: async_sessionmaker[AsyncSession], w: World, type_: str) -> list[HouseEvent]:
    async with sessions() as db:
        return list(
            (
                await db.scalars(
                    select(HouseEvent)
                    .where(HouseEvent.house_id == w.hid, HouseEvent.type == type_)
                    .order_by(HouseEvent.seq)
                )
            ).all()
        )


async def order(who: Api, w: World, location: str, ids: list[str]) -> Any:
    return await who.call("PUT", f"/api/v1/houses/{w.hid}/me/item-order/{location}", {"item_ids": ids})


def area_ids(view: dict[str, Any], area: str) -> list[str]:
    return [i["id"] for a in view["areas"] if a["area"] == area for i in a["items"]]


@tid("GEST-14")
@fr("FR-G03", "FR-G05", "FR-G08")
async def test_GEST_14_move_results_idempotency_limits_and_ex_members(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, p = await kitchen_world(api)
    view = await fridge(w.admin, w)
    milk, eggs, yogurt = (find(view, n)["id"] for n in ("Whole milk", "Eggs", "Greek yogurt"))
    rice = find(await fridge(w.admin, w, "pantry"), "Basmati rice")["id"]
    await p["arjun"].post(f"/api/v1/inventory/{eggs}/used-up")

    # Kabir owns none of these: location is physical, so any member may move them.
    key = str(uuid.uuid4())
    r = await move(
        p["kabir"],
        w,
        {"item_id": milk, "to": "pantry"},
        {"item_id": rice, "to": "pantry"},
        {"item_id": eggs, "to": "pantry"},
        {"item_id": str(uuid7()), "to": "pantry"},
        key=key,
    )
    assert r.status_code == 200, r.text
    results = [x["result"] for x in r.json()["results"]]
    assert results == ["moved", "skipped_already_there", "skipped_gone", "skipped_gone"]
    assert r.json()["moved"] == 1
    assert [x["from"] for x in r.json()["results"]] == ["fridge", None, None, None], "Undo goes back here"
    assert await place_of(w, milk) == "pantry"
    moved_milk = find(await fridge(w.admin, w, "pantry"), "Whole milk")
    assert moved_milk["moved"]["by"] == "Kabir", "the sheet's 'Moved to pantry by Kabir' line"

    # A retry with the same key replays the answer and writes nothing (SYNC-G3 shape).
    again = await move(
        p["kabir"],
        w,
        {"item_id": milk, "to": "pantry"},
        {"item_id": rice, "to": "pantry"},
        {"item_id": eggs, "to": "pantry"},
        {"item_id": r.json()["results"][3]["item_id"], "to": "pantry"},
        key=key,
    )
    assert again.status_code == 200 and again.json() == r.json()
    assert len(await events(sessions, w, "items.moved")) == 1

    # Undo: inverse moves guarded by expect_from; someone moved it since → skipped_changed.
    await move(p["meera"], w, {"item_id": milk, "to": "freezer"})
    undo = await move(p["kabir"], w, {"item_id": milk, "to": "fridge", "expect_from": "pantry"})
    assert [x["result"] for x in undo.json()["results"]] == ["skipped_changed"]
    assert await place_of(w, milk) == "freezer"
    back = await move(p["meera"], w, {"item_id": milk, "to": "fridge", "expect_from": "freezer"})
    assert [x["result"] for x in back.json()["results"]] == ["moved"]

    # 1..20 moves, each item once, real places only.
    too_many = [{"item_id": str(uuid7()), "to": "pantry"} for _ in range(21)]
    assert (await move(w.admin, w, *too_many)).status_code == 422
    assert (await move(w.admin, w)).status_code == 422
    assert (
        await move(w.admin, w, {"item_id": yogurt, "to": "pantry"}, {"item_id": yogurt, "to": "freezer"})
    ).status_code == 422
    assert (await move(w.admin, w, {"item_id": yogurt, "to": "garage"})).status_code == 422
    assert (
        await w.admin.call(
            "POST", f"/api/v1/houses/{w.hid}/items/move", {"moves": [{"item_id": yogurt, "to": "pantry"}]}, idem=False
        )
    ).status_code == 400

    # Members who left can't move anything; outsiders can't either.
    assert (await p["meera"].post(f"/api/v1/houses/{w.hid}/leave")).status_code == 200
    gone = await move(p["meera"], w, {"item_id": yogurt, "to": "pantry"})
    assert gone.status_code == 403
    stranger = api()
    await stranger.sign_in(f"stranger+{uuid7()}@example.com")
    assert (await move(stranger, w, {"item_id": yogurt, "to": "pantry"})).status_code == 403
    assert await place_of(w, yogurt) == "fridge"


@pytest.fixture
async def moves_limited(settings: Settings, db_reset: None) -> AsyncIterator[Callable[[], Api]]:
    """The app with the production move limit (the shared test app turns it off for SYNC x200)."""
    app = create_app(settings.model_copy(update={"rate_limit_moves_per_min": 60}))
    clients: list[httpx.AsyncClient] = []

    def make() -> Api:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)
        clients.append(c)
        return Api(c)

    async with app.router.lifespan_context(app):
        yield make
    for c in clients:
        await c.aclose()


@tid("GEST-14", "SEC-05")
@fr("FR-G03")
async def test_GEST_14_sixty_move_requests_a_minute(moves_limited: Callable[[], Api]) -> None:
    who = moves_limited()
    await who.sign_in(f"mover+{uuid7()}@example.com")
    hid = (await who.create_house())["house"]["id"]
    item = (await who.post(f"/api/v1/houses/{hid}/inventory", {"name": "Dal", "place": "fridge"})).json()["id"]
    await who.post("/__test/clock", {"now": "2026-10-09T14:00:00-04:00"}, idem=False)
    statuses = []
    for n in range(61):
        to = "pantry" if n % 2 == 0 else "fridge"
        r = await who.post(f"/api/v1/houses/{hid}/items/move", {"moves": [{"item_id": item, "to": to}]})
        statuses.append(r.status_code)
    assert statuses[:60] == [200] * 60 and statuses[60] == 429


@tid("GEST-15", "SYNC-G6")
@fr("FR-G02")
async def test_GEST_15_my_order_is_mine_per_section_and_stale_ids_are_skipped(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, p = await kitchen_world(api)
    house_order = area_ids(await fridge(w.admin, w), "fridge")
    mine = list(reversed(house_order))
    eggs = find(await fridge(w.admin, w), "Eggs")["id"]
    stale = str(uuid7())
    r = await order(p["arjun"], w, "fridge", [mine[0], stale, *mine[1:]])
    assert r.status_code == 200, r.text
    assert area_ids(await fridge(p["arjun"], w), "fridge") == mine, "stale ids skipped, no error"
    assert area_ids(await fridge(w.admin, w), "fridge") == house_order, "only Arjun's view changes"
    assert area_ids(await fridge(p["arjun"], w), "freezer") == area_ids(await fridge(w.admin, w), "freezer"), (
        "per section"
    )

    # Used up drops out of the order; new stock from a new bill lands after my order (SYNC-G6).
    await p["arjun"].post(f"/api/v1/inventory/{eggs}/used-up")
    b2, ids2 = await draft_with_items(w)
    await tick_like_fixture(w, b2, ids2)
    await w.admin.post(f"/api/v1/bills/{b2}/send")
    after = area_ids(await fridge(p["arjun"], w), "fridge")
    kept = [i for i in mine if i != eggs]
    assert after[: len(kept)] == kept
    assert len(after) > len(kept) and not set(after[len(kept) :]) & set(kept)

    # Set-state: the same list again writes nothing; the event names only the member and section.
    before = len(await events(sessions, w, "member.item_order.set"))
    assert (await order(p["arjun"], w, "fridge", [mine[0], stale, *mine[1:]])).status_code == 200
    evs = await events(sessions, w, "member.item_order.set")
    assert len(evs) == before
    assert evs[-1].data == {"member_id": w.member["arjun"], "location": "fridge"}

    assert (await order(p["arjun"], w, "fridge", [eggs, eggs])).status_code == 422
    assert (await order(p["arjun"], w, "garage", [])).status_code == 422
    assert (await order(p["arjun"], w, "fridge", [])).status_code == 200
    assert area_ids(await fridge(p["arjun"], w), "fridge") == area_ids(await fridge(w.admin, w), "fridge"), (
        "an empty order falls back to the usual one"
    )


# ---- races (SPEC §5.3), SYNC_ITERATIONS each, both PROFILEs via make test-sync -------------------


@pytest.mark.sync
@tid("SYNC-G1")
@fr("FR-G03")
async def test_SYNC_G1_two_members_move_the_same_item_to_different_places(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, p = await kitchen_world(api)
    milk = find(await fridge(w.admin, w), "Whole milk")["id"]
    for _ in range(sync_iterations()):
        await move(w.admin, w, {"item_id": milk, "to": "fridge"})
        out = await race(
            lambda: move(p["arjun"], w, {"item_id": milk, "to": "pantry"}),
            lambda: move(p["meera"], w, {"item_id": milk, "to": "freezer"}),
        )
        assert codes(out) == [200, 200]
        assert [r.json()["results"][0]["result"] for r in out] == ["moved", "moved"], "both succeed in lock order"
        last = (await events(sessions, w, "items.moved"))[-1]
        assert last.data is not None
        assert await place_of(w, milk) == last.data["moves"][0]["place"], "the later one wins"


@pytest.mark.sync
@tid("SYNC-G2")
@fr("FR-G06")
async def test_SYNC_G2_used_up_while_in_someone_elses_stack(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    names = ("Whole milk", "Greek yogurt", "Chicken thighs")
    for _ in range(sync_iterations()):
        view = await fridge(w.admin, w)
        milk, yogurt, chicken = (find(view, x)["id"] for x in names)
        out = await race(
            lambda: move(
                p["kabir"],
                w,
                {"item_id": milk, "to": "pantry"},
                {"item_id": yogurt, "to": "pantry"},
                {"item_id": chicken, "to": "pantry"},
            ),
            lambda: p["meera"].post(f"/api/v1/inventory/{yogurt}/used-up"),
        )
        assert codes(out) == [200, 200]
        by_id = {x["item_id"]: x["result"] for x in out[0].json()["results"]}
        assert by_id[milk] == by_id[chicken] == "moved", "the rest of the stack moves"
        assert by_id[yogurt] in ("moved", "skipped_gone")
        assert await place_of(w, yogurt) == "gone"
        # Reset for the next round.
        await p["meera"].post(f"/api/v1/inventory/{yogurt}/restore")
        await move(w.admin, w, *({"item_id": i, "to": "fridge"} for i in (milk, yogurt, chicken)))


@pytest.mark.sync
@tid("SYNC-G3")
@fr("FR-G06")
async def test_SYNC_G3_lost_response_retry_gives_one_result_and_one_event(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, p = await kitchen_world(api)
    view = await fridge(w.admin, w)
    milk, yogurt = find(view, "Whole milk")["id"], find(view, "Greek yogurt")["id"]
    for i in range(sync_iterations()):
        to = "pantry" if i % 2 == 0 else "fridge"
        key = str(uuid.uuid4())
        moves = ({"item_id": milk, "to": to}, {"item_id": yogurt, "to": to})
        before = len(await events(sessions, w, "items.moved"))
        out = await race(lambda: move(p["arjun"], w, *moves, key=key), lambda: move(p["arjun"], w, *moves, key=key))
        assert codes(out) == [200, 200]
        assert out[0].json() == out[1].json()
        assert len(await events(sessions, w, "items.moved")) == before + 1


@pytest.mark.sync
@tid("SYNC-G4")
@fr("FR-G08")
async def test_SYNC_G4_undo_after_someone_moved_it_again(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    milk = find(await fridge(w.admin, w), "Whole milk")["id"]
    for _ in range(sync_iterations()):
        await move(w.admin, w, {"item_id": milk, "to": "fridge"})
        await move(p["arjun"], w, {"item_id": milk, "to": "pantry"})
        out = await race(
            lambda: move(p["arjun"], w, {"item_id": milk, "to": "fridge", "expect_from": "pantry"}),
            lambda: move(p["meera"], w, {"item_id": milk, "to": "freezer"}),
        )
        assert codes(out) == [200, 200]
        undo = out[0].json()["results"][0]["result"]
        assert undo in ("moved", "skipped_changed")
        assert await place_of(w, milk) == "freezer", "Meera's move stands either way"


@pytest.mark.sync
@tid("SYNC-G5")
@fr("FR-G02")
async def test_SYNC_G5_reorder_on_phone_and_laptop_at_once(api: ApiFactory) -> None:
    w, _, _, _ = await kitchen_world(api)
    email = f"lena+{uuid7()}@example.com"
    added = await w.admin.post(f"/api/v1/houses/{w.hid}/members", {"name": "Lena"})
    assert added.status_code == 201, added.text
    phone, laptop = api(), api()
    for device in (phone, laptop):
        await device.sign_in(email)
    claim = await phone.post(f"/api/v1/join/{w.house['invite_token']}/claim", {"member_id": added.json()["id"]})
    assert claim.status_code == 200, claim.text
    ids = area_ids(await fridge(w.admin, w), "fridge")
    a, b = list(reversed(ids)), ids[1:] + ids[:1]
    for _ in range(sync_iterations()):
        out = await race(lambda: order(phone, w, "fridge", a), lambda: order(laptop, w, "fridge", b))
        assert codes(out) == [200, 200]
        on_phone = area_ids(await fridge(phone, w), "fridge")
        assert on_phone in (a, b), "last write wins, whole lists only"
        assert area_ids(await fridge(laptop, w), "fridge") == on_phone, "both devices converge"


@tid("DATA-06")
@fr("FR-G03")
async def test_moves_write_one_event_and_one_activity(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, _, _, p = await kitchen_world(api)
    view = await fridge(w.admin, w)
    ids = [find(view, n)["id"] for n in ("Whole milk", "Greek yogurt", "Chicken thighs")]
    async with sessions() as db:
        before = await db.scalar(select(func.count()).select_from(HouseEvent).where(HouseEvent.house_id == w.hid))
    r = await move(p["arjun"], w, *({"item_id": i, "to": "pantry"} for i in ids))
    assert r.json()["moved"] == 3
    async with sessions() as db:
        after = await db.scalar(select(func.count()).select_from(HouseEvent).where(HouseEvent.house_id == w.hid))
    assert after == (before or 0) + 1, "three moves, one event (GEST-10)"


@tid("GEST-15", "KITCH-07")
@fr("FR-G02", "FR-66")
async def test_GEST_15_tiles_never_reshuffle_after_an_unrelated_write(api: ApiFactory) -> None:
    w, _, _, p = await kitchen_world(api)
    for n in range(4):  # items stocked together tie on every sort key but the id
        await w.admin.post(f"/api/v1/houses/{w.hid}/inventory", {"name": f"Soup {n}", "place": "freezer"})
    before = await fridge(w.admin, w)
    soups = [i for i in area_ids(before, "freezer")]
    milk = find(before, "Whole milk")["id"]
    for to in ("pantry", "fridge"):
        await move(p["arjun"], w, {"item_id": milk, "to": to})
        after = await fridge(w.admin, w)
        assert area_ids(after, "freezer") == soups
    assert area_ids(await fridge(w.admin, w), "fridge") == area_ids(before, "fridge")
