"""DATA-06: every state change writes exactly one `house_events` row, and every activity entry
belongs to exactly one of them (PRD §21.2). Walks one write of each kind on an open bill and in
the kitchen and checks the log after each."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ActivityEvent, HouseEvent
from tests.harness.api import Api
from tests.harness.tags import fr, tid
from tests.test_bills import KEYS
from tests.test_kitchen import find, fridge, kitchen_world
from tests.test_sync_api import tick

ApiFactory = Callable[[], Api]


async def _log(sessions: async_sessionmaker[AsyncSession]) -> tuple[list[tuple[int, str]], list[int | None]]:
    async with sessions() as db:
        events = [(s, t) for s, t in (await db.execute(select(HouseEvent.seq, HouseEvent.type))).all()]
        acts = list((await db.scalars(select(ActivityEvent.house_event_seq))).all())
    return sorted(events), acts


@tid("DATA-06")
@fr("FR-43")
async def test_DATA_06_one_event_per_state_change_and_activity_one_to_one(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w, bill_id, ids, p = await kitchen_world(api)
    milk = find(await fridge(w.admin, w), "Whole milk")
    chicken = find(await fridge(p["kabir"], w), "Chicken thighs")
    ask_id: dict[str, Any] = {}

    async def ask() -> Any:
        r = await p["kabir"].post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
        ask_id["id"] = r.json()["id"]
        return r

    # (write, expected event types, expected activity entries)
    steps: list[tuple[Callable[[], Awaitable[Any]], list[str], int]] = [
        (
            lambda: tick(p["meera"], bill_id, ids[KEYS.index("coffee")], w.key("meera"), False),
            ["allocation_changed"],
            1,
        ),
        (
            lambda: p["arjun"].post(
                f"/api/v1/bills/{bill_id}/payments",
                {"participant": w.key("arjun"), "amount_cents": 1000, "method": "venmo"},
            ),
            ["payment_recorded"],
            1,
        ),
        (
            lambda: w.admin.post(f"/api/v1/bills/{bill_id}/remind", {"member_id": w.member["meera"]}),
            ["reminder_sent"],
            1,
        ),
        (
            lambda: w.admin.call("PUT", f"/api/v1/inventory/{milk['id']}/sharing", {"open": True}),
            ["inventory_changed"],
            1,
        ),
        (ask, ["request_created"], 1),
        (
            lambda: w.admin.post(f"/api/v1/requests/{ask_id['id']}/answer", {"decision": "split"}),
            ["allocation_changed", "request_answered"],
            2,
        ),
        (lambda: w.admin.post(f"/api/v1/inventory/{milk['id']}/used-up"), ["inventory_changed"], 1),
        (lambda: w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True}), ["bill_settled"], 1),
    ]
    for write, types, activities in steps:
        before_events, before_acts = await _log(sessions)
        r = await write()
        assert r.status_code in (200, 201), r.text
        after_events, after_acts = await _log(sessions)
        new = after_events[len(before_events) :]
        assert [t for _, t in new] == types, f"one event per state change: {types}"
        assert len(after_acts) - len(before_acts) == activities == len(types), "one activity per event"
        assert {a for a in after_acts if a not in before_acts} <= {s for s, _ in new}
    # A no-op set-state write changes nothing and logs nothing.
    before = await _log(sessions)
    again = await w.admin.call("PUT", f"/api/v1/inventory/{milk['id']}/sharing", {"open": True})
    assert again.status_code == 200 and await _log(sessions) == before
    # Every activity entry points at exactly one event; no event carries two entries.
    async with sessions() as db:
        orphans = await db.scalar(
            select(func.count()).select_from(ActivityEvent).where(ActivityEvent.house_event_seq.is_(None))
        )
        dupes = await db.scalar(
            select(func.count()).select_from(
                select(ActivityEvent.house_id, ActivityEvent.house_event_seq)
                .group_by(ActivityEvent.house_id, ActivityEvent.house_event_seq)
                .having(func.count() > 1)
                .subquery()
            )
        )
    assert (orphans, dupes) == (0, 0)
