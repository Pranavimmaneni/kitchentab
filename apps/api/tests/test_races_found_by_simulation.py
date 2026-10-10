"""Races the live household simulation found (BUILD_ALL §4.3), pinned down deterministically.

SYNC-35: a view read in pieces under READ COMMITTED could see a write land between its SELECTs (an
old total next to new prices) and fail with a 500 trying to reconcile it. Views now read one snapshot.

PUSH-19: device bookkeeping (last success, failure count) was written inside the drain's batch
transaction, so its row locks lasted until the whole batch committed: two drains delivering to the
same person deadlocked, the victim's error handler crashed, the drain failed, and its batch, already
pushed, went out again on the next drain.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Notification, PushSubscription
from app.services import bill_math
from app.services import notify as notify_svc
from fakeservices.fake_push import create_app as create_fake_push
from tests.harness.api import Api
from tests.harness.servers import free_port, serve
from tests.harness.tags import fr, tid
from tests.test_notify import Phone, drain, household
from tests.test_sync_api import open_bill

ApiFactory = Callable[[], Api]


@pytest.fixture(scope="module")
def fake_push() -> Iterator[str]:
    port = free_port()
    with serve(create_fake_push(f"http://127.0.0.1:{port}"), port) as url:
        yield url


@tid("SYNC-35")
@fr("FR-52")
async def test_SYNC_35_a_price_edit_committing_mid_read_never_tears_a_view(
    api: ApiFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, bill_id, _, _ = await open_bill(api)
    real_load = bill_math.load
    armed = {"on": False}

    async def edit_lands_mid_read(db: AsyncSession, bill: Any) -> bill_math.BillRows:
        if armed["on"]:  # the payer's edit commits after the view's first SELECT, before its items
            armed["on"] = False
            item = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["items"][0]
            r = await w.admin.call(
                "PATCH",
                f"/api/v1/bills/{bill_id}/items/{item['id']}",
                {"line_total_cents": item["line_total_cents"] + 37},
                if_match=item["version"],
            )
            assert r.status_code == 200, r.text
        return await real_load(db, bill)

    monkeypatch.setattr(bill_math, "load", edit_lands_mid_read)
    for path in (f"/api/v1/houses/{w.hid}/tabs", f"/api/v1/bills/{bill_id}/money", f"/api/v1/bills/{bill_id}"):
        armed["on"] = True
        r = await w.admin.get(path)
        assert not armed["on"], f"{path} never loaded the bill"
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        if path.endswith(bill_id):  # the bill view: shares add up to its own total
            view = r.json()
            assert sum(view["shares"].values()) == view["bill"]["total_cents"]
    tabs = (await w.admin.get(f"/api/v1/houses/{w.hid}/tabs")).json()
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert next(t for t in tabs["open"] if t["id"] == bill_id)["total_cents"] == view["bill"]["total_cents"]


def assert_once(phone: Phone) -> None:
    got = [(p["event_id"], p["tag"]) for p in phone.payloads()]
    assert len(got) == len(set(got)), f"{phone.label} got a push twice"


class Watching:
    """The real sender, plus a hook that runs before each send while the drain's batch is open."""

    def __init__(self, real: Any, hook: Callable[[set[str]], Any]) -> None:
        self.real, self.hook, self.sent_to = real, hook, set[str]()

    async def send(self, sub: Any, payload: dict[str, Any], **kw: Any) -> Any:
        await self.hook(self.sent_to)
        result = await self.real.send(sub, payload, **kw)
        self.sent_to.add(sub.endpoint)
        return result


@tid("PUSH-19")
async def test_PUSH_19_a_drain_holds_no_device_locks_while_its_batch_is_open(
    api: ApiFactory,
    fake_push: str,
    app: Any,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _, _ = await household(api, fake_push, send=False)
    laptop = Phone(fake_push, f"{h.phones['arjun'].label}-laptop")
    await laptop.register(h.people["arjun"])
    for _ in range(2):  # one batch: arjun's phone and laptop, then both again
        assert (await h.people["arjun"].post("/api/v1/push/test")).status_code == 202
    async with sessions() as db:
        laptop_sub = await db.scalar(select(PushSubscription.id).where(PushSubscription.endpoint == laptop.endpoint))
    checked: list[bool] = []
    waited: list[str] = []

    async def another_drain_touches_the_device(sent_to: set[str]) -> None:
        if laptop.endpoint not in sent_to or checked:  # only once arjun's laptop has had its push
            return
        checked.append(True)
        async with sessions() as db, db.begin():
            await db.execute(text("SET LOCAL lock_timeout = '2s'"))
            try:
                await db.execute(
                    text("UPDATE push_subscriptions SET failure_count = failure_count WHERE id = :id"),
                    {"id": laptop_sub},
                )
            except Exception as exc:  # a lock the batch still holds
                waited.append(type(exc).__name__)

    monkeypatch.setattr(app.state.notify, "push", Watching(app.state.notify.push, another_drain_touches_the_device))
    await drain(h.w.admin)
    assert checked, "the batch never sent anything after arjun's laptop"
    assert waited == [], "the drain's batch held a device row lock: concurrent drains deadlock"
    assert len(h.phones["arjun"].payloads()) == len(laptop.payloads()) == 2
    for phone in (*h.phones.values(), laptop):
        assert_once(phone)


@tid("PUSH-19")
async def test_PUSH_19_one_delivery_failing_mid_batch_never_resends_the_batch(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _, _ = await household(api, fake_push)
    real_badge = notify_svc._badge
    blew_up: list[str] = []

    async def badge_breaks_once(db: AsyncSession, user_id: Any, now: Any) -> int:
        if not blew_up:  # e.g. a deadlock victim: the savepoint rolls back
            blew_up.append(str(user_id))
            await db.execute(text("SELECT 1/0"))
        return await real_badge(db, user_id, now)

    monkeypatch.setattr(notify_svc, "_badge", badge_breaks_once)
    await drain(h.w.admin)  # 200: one row failed, the rest went out
    monkeypatch.setattr(notify_svc, "_badge", real_badge)
    await drain(h.w.admin)  # nothing left to send again
    async with sessions() as db:
        failed = (
            await db.scalars(
                select(Notification).where(Notification.channel == "push", Notification.status == "failed")
            )
        ).all()
    assert len(failed) == 1 and failed[0].last_error == "exception"
    for phone in h.phones.values():
        assert_once(phone)
    async with sessions() as db:
        sent = await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.channel == "push", Notification.status == "sent")
        )
    # one phone each: every push row marked sent reached its phone exactly once, the failed one never
    assert sum(len(p.payloads()) for p in h.phones.values()) == sent
