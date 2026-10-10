"""Notifications (PRD §20.2; TEST_PLAN 3.9): outbox → fan-out → Web Push / email / inbox.

Push runs the app's real sender (RFC 8291 + VAPID) against the fake push service, which verifies
and decrypts every message (TEST_PLAN §2). The drain is driven with the controllable clock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.models import Notification, OutboxRow, PushSubscription
from fakeservices.fake_push import create_app
from tests.harness.api import TEST_INBOX_SECRET, Api
from tests.harness.servers import free_port, serve
from tests.harness.tags import fr, tid
from tests.test_bills import KEYS, World, draft_with_items, tick_like_fixture
from tests.test_sync_api import tick

ApiFactory = Callable[[], Api]
DAYTIME = "2026-10-09T14:00:00-04:00"  # outside quiet hours in New York
NAMES = ("arjun", "meera", "kabir")


@pytest.fixture(scope="module")
def fake_push() -> Iterator[str]:
    port = free_port()
    with serve(create_app(f"http://127.0.0.1:{port}"), port) as url:
        yield url


class Phone:
    """One device subscribed at the fake push service and registered with the API."""

    def __init__(self, base: str, label: str) -> None:
        self.base, self.label = base, label

    async def register(self, who: Api, device: str | None = None) -> dict[str, Any]:
        data = httpx.post(f"{self.base}/subscriptions", json={"user": self.label}).json()
        body = {"endpoint": data["endpoint"], "keys": data["keys"]}
        if device:
            body["device_label"] = device
        r = await who.post("/api/v1/push/subscriptions", body, idem=False)
        assert r.status_code == 201, r.text
        self.sub_id = data["id"]
        self.endpoint = data["endpoint"]
        result: dict[str, Any] = r.json()
        return result

    def received(self) -> list[dict[str, Any]]:
        got: list[dict[str, Any]] = httpx.get(f"{self.base}/__test/push", params={"user": self.label}).json()
        return got

    def payloads(self) -> list[dict[str, Any]]:
        return [r["payload"] for r in self.received()]


async def at(who: Api, when: str) -> None:
    assert (await who.post("/__test/clock", {"now": when}, idem=False)).status_code == 200


async def advance(who: Api, seconds: int) -> None:
    await who.post("/__test/clock", {"advance_seconds": seconds}, idem=False)


async def drain(who: Api) -> dict[str, int]:
    r = await who.post("/__test/drain", idem=False)
    assert r.status_code == 200, r.text
    data: dict[str, int] = r.json()
    return data


async def emails(who: Api, to: str) -> list[dict[str, Any]]:
    r = await who.http.get("/api/test/inbox", params={"to": to}, headers={"X-Test-Secret": TEST_INBOX_SECRET})
    got: list[dict[str, Any]] = r.json()
    return got


class Household:
    def __init__(self, w: World, people: dict[str, Api], phones: dict[str, Phone]) -> None:
        self.w, self.people, self.phones = w, people, phones
        self.everyone = {"pranav": w.admin, **people}


async def household(api: ApiFactory, fake_push: str, *, send: bool = True) -> tuple[Household, str, list[str]]:
    """Everyone has claimed their spot and has one phone before the ShopRite tab is sent."""
    admin = api()
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    await at(admin, DAYTIME)
    w = World(admin, await admin.create_house())
    people: dict[str, Api] = {}
    for name in NAMES:
        who = api()
        await who.sign_in(f"{name}+{uuid7()}@example.com")
        r = await who.post(f"/api/v1/join/{w.house['invite_token']}/claim", {"member_id": w.member[name]})
        assert r.status_code == 200, r.text
        people[name] = who
    tag = uuid.uuid4().hex[:6]
    phones = {n: Phone(fake_push, f"{n}-{tag}") for n in ("pranav", *NAMES)}
    h = Household(w, people, phones)
    for n, phone in phones.items():
        await phone.register(h.everyone[n])
    bill_id, ids = await draft_with_items(w)
    await tick_like_fixture(w, bill_id, ids)
    if send:
        assert (await admin.post(f"/api/v1/bills/{bill_id}/send")).status_code == 200
    return h, bill_id, ids


@tid("PUSH-03", "PUSH-04")
async def test_PUSH_03_04_devices_one_user_many_and_endpoint_changes(api: ApiFactory, fake_push: str) -> None:
    me = api()
    await me.sign_in(f"devices+{uuid7()}@example.com")
    iphone, mac = Phone(fake_push, f"iphone-{uuid7()}"), Phone(fake_push, f"mac-{uuid7()}")
    first = await iphone.register(me, "This iPhone")
    await mac.register(me)
    again = await me.post(
        "/api/v1/push/subscriptions",
        {"endpoint": iphone.endpoint, "keys": {"p256dh": "B" * 87, "auth": "A" * 22}},
        idem=False,
    )
    assert again.json()["id"] == first["id"], "same endpoint updates in place"
    devices = (await me.get("/api/v1/push/subscriptions")).json()
    assert [d["device_label"] for d in devices] == ["This iPhone", "This browser"]
    # pushsubscriptionchange: the browser hands a new endpoint; the old row goes.
    newer = Phone(fake_push, iphone.label)
    data = httpx.post(f"{fake_push}/subscriptions", json={"user": newer.label}).json()
    r = await me.post(
        "/api/v1/push/subscriptions",
        {"endpoint": data["endpoint"], "keys": data["keys"], "replaces_endpoint": iphone.endpoint},
        idem=False,
    )
    assert r.status_code == 201
    devices = (await me.get("/api/v1/push/subscriptions")).json()
    assert len(devices) == 2 and first["id"] not in {d["id"] for d in devices}


async def test_device_labels_from_user_agent() -> None:
    from app.routes.notify import device_label

    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari"
    mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
    assert (device_label(iphone), device_label(mac), device_label("curl/8")) == (
        "This iPhone",
        "Chrome on Mac",
        "This browser",
    )


@tid("PUSH-06", "PUSH-07", "PUSH-18")
async def test_PUSH_06_every_event_type_reaches_the_right_people(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    h, bill_id, ids = await household(api, fake_push)
    w, p, ph = h.w, h.people, h.phones
    await drain(w.admin)
    # New tab includes you: every member but the payer (who sent it).
    tab = ph["arjun"].payloads()[-1]
    assert tab == {
        "title": "Pranav split a ShopRite run",
        "body": "Your share is $33.91. Tick anything that's wrong.",
        "url": f"/bills/{bill_id}",
        "tag": f"bill-{bill_id}",
        "badge": 0,
        "event_id": tab["event_id"],
    }
    titles = [x["title"] for x in ph["pranav"].payloads()]
    assert sorted(titles) == ["Arjun joined Apt 4B", "Kabir joined Apt 4B", "Meera joined Apt 4B"]
    assert not any("split a" in t for t in titles), "never the actor (PUSH-07)"
    # Share changed by ≥ 50¢: Meera joins the coffee; Pranav and Kabir hear after the 10-minute window.
    await tick(p["meera"], bill_id, ids[KEYS.index("coffee")], w.key("meera"), True)
    await drain(w.admin)

    def shares(name: str) -> list[dict[str, Any]]:
        return [x for x in ph[name].payloads() if x["title"].endswith("share changed")]

    assert shares("pranav") == [], "coalescing window still open"
    await advance(w.admin, 601)
    await drain(w.admin)
    [share] = shares("pranav")
    assert (share["title"], share["body"]) == (
        "Your ShopRite share changed",
        "Meera joined the coffee beans. You're now at $21.82 (−$1.69).",
    )
    assert share["badge"] == 1, "an unseen share change counts on the badge"
    assert not any(x["title"].endswith("share changed") for x in ph["meera"].payloads()), "not the actor"
    # Payment recorded → payer; confirmed → member.
    paid = await p["kabir"].post(
        f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("kabir"), "amount_cents": 1721, "method": "venmo"}
    )
    assert paid.status_code == 201, paid.text
    await drain(w.admin)
    assert ph["pranav"].payloads()[-1]["title"] == "Kabir paid $17.21"
    assert ph["pranav"].received()[-1]["urgency"] == "high"
    await w.admin.post(f"/api/v1/payments/{paid.json()['id']}/confirm")
    await drain(w.admin)
    assert ph["kabir"].payloads()[-1]["title"] == "Pranav confirmed your $17.21"
    # Reminder → the unpaid member only.
    await w.admin.post(f"/api/v1/bills/{bill_id}/remind", {"member_id": w.member["arjun"]})
    await drain(w.admin)
    nudge = ph["arjun"].payloads()[-1]
    assert (nudge["title"], nudge["body"], nudge["tag"]) == (
        "Quick nudge from Pranav",
        "ShopRite · your share is $33.91",
        f"remind-{bill_id}",
    )
    # Ask received → every owner; answered → the asker.
    inv = (await p["kabir"].get(f"/api/v1/houses/{w.hid}/inventory", params={"place": "fridge"})).json()
    chicken = next(i for a in inv["areas"] for i in a["items"] if i["name"].startswith("Chicken"))
    ask = await p["kabir"].post(
        f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit", "note": "For tonight's curry"}
    )
    await drain(w.admin)
    for owner in ("pranav", "arjun"):
        got = ph[owner].received()[-1]
        assert got["payload"]["title"] == "Kabir asked for your chicken thighs"
        assert got["payload"]["body"] == "A bit, for tonight's curry. Tap to answer."
        assert (got["ttl"], got["urgency"]) == (48 * 3600, "high")
    assert not any("asked for" in x["title"] for x in ph["meera"].payloads())
    await p["arjun"].post(f"/api/v1/requests/{ask.json()['id']}/answer", {"decision": "split"})
    await drain(w.admin)
    answered = ph["kabir"].payloads()[-1]
    assert (answered["title"], answered["body"]) == ("Arjun said yes to chicken thighs", "Added to the split (+$3.83)")
    # Settled → everyone on the tab but the actor.
    await advance(w.admin, 601)
    await drain(w.admin)
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    await drain(w.admin)
    for name in NAMES:  # N2 defaults: settled is email and inbox, not push
        assert not any(x["title"] == "ShopRite is settled" for x in ph[name].payloads())
        inbox = (await p[name].get("/api/v1/me/inbox")).json()["items"]
        assert inbox[0]["title"] == "ShopRite is settled"
        mail = [m for m in await emails(p[name], p[name].email or "") if m["subject"] == "ShopRite is settled"]
        assert len(mail) == 1
    pranav_inbox = (await w.admin.get("/api/v1/me/inbox")).json()["items"]
    assert not any(x["title"] == "ShopRite is settled" for x in pranav_inbox), "never the actor"
    # Every payload is small, encrypted and signed (the fake service verified VAPID and decrypted it).
    for phone in ph.values():
        for got in phone.received():
            assert got["plaintext_bytes"] <= 2048 and got["encrypted_bytes"] > got["plaintext_bytes"]
            assert got["ttl"] in (24 * 3600, 48 * 3600)
    async with sessions() as db:
        pending = await db.scalar(select(func.count()).select_from(OutboxRow).where(OutboxRow.status != "done"))
    assert pending == 0


@tid("PUSH-06")
async def test_PUSH_06_someone_joined_goes_to_admins(api: ApiFactory, fake_push: str) -> None:
    admin = api()
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    await at(admin, DAYTIME)
    house = await admin.create_house()
    phone = Phone(fake_push, f"admin-{uuid7()}")
    await phone.register(admin)
    meera = api()
    await meera.sign_in(f"meera+{uuid7()}@example.com")
    member = next(m for m in house["members"] if m["display_name"] == "Meera")
    await meera.post(f"/api/v1/join/{house['invite_token']}/claim", {"member_id": member["id"]})
    await drain(admin)
    [joined] = phone.payloads()
    assert (joined["title"], joined["url"]) == (f"Meera joined {house['house']['name']}", "/house")


@tid("PUSH-08")
async def test_PUSH_08_private_on_the_lock_screen(api: ApiFactory, fake_push: str) -> None:
    h, _, _ = await household(api, fake_push, send=False)
    await h.people["arjun"].call("PUT", "/api/v1/me/notification-prefs", {"private_lock_screen": True})
    bill = (await h.w.admin.get(f"/api/v1/houses/{h.w.hid}/tabs")).json()["drafts"][0]["id"]
    await h.w.admin.post(f"/api/v1/bills/{bill}/send")
    await drain(h.w.admin)
    [got] = h.phones["arjun"].payloads()
    assert (got["title"], got["body"]) == ("A new tab includes you", "Open KitchenTab to see it.")
    shown = got["title"] + got["body"]
    assert "33" not in shown and "Pranav" not in shown and "ShopRite" not in shown


@tid("PUSH-09")
async def test_PUSH_09_preferences_matrix_per_type_and_channel(api: ApiFactory, fake_push: str) -> None:
    h, _, _ = await household(api, fake_push, send=False)
    arjun, meera = h.people["arjun"], h.people["meera"]
    defaults = (await arjun.get("/api/v1/me/notification-prefs")).json()
    assert defaults["push"]["settled"] is False and defaults["email"]["tab"] is True, "N2 defaults"
    await arjun.call("PUT", "/api/v1/me/notification-prefs", {"push": {"tab": False}, "email": {"tab": False}})
    await meera.call("PUT", "/api/v1/me/notification-prefs", {"push": {"tab": False}})
    bad = await meera.call("PUT", "/api/v1/me/notification-prefs", {"push": {"bogus": True}})
    assert bad.status_code == 422
    bill = (await h.w.admin.get(f"/api/v1/houses/{h.w.hid}/tabs")).json()["drafts"][0]["id"]
    await h.w.admin.post(f"/api/v1/bills/{bill}/send")
    await drain(h.w.admin)
    assert h.phones["arjun"].payloads() == [] and h.phones["meera"].payloads() == []
    assert len(h.phones["kabir"].payloads()) == 1
    assert [m for m in await emails(arjun, arjun.email or "") if "split a ShopRite run" in m["subject"]] == []
    [mail] = [m for m in await emails(meera, meera.email or "") if "split a ShopRite run" in m["subject"]]
    assert "Your share is $18.87" in mail["text"] and "/bills/" in mail["text"]
    inbox = (await arjun.get("/api/v1/me/inbox")).json()
    assert inbox["items"][0]["title"] == "Pranav split a ShopRite run", "turning a type off never hides it in the app"


@tid("PUSH-10")
async def test_PUSH_10_quiet_hours_hold_and_group_until_8(api: ApiFactory, fake_push: str) -> None:
    h, _, _ = await household(api, fake_push)
    await drain(h.w.admin)
    kabir = h.people["kabir"]
    await at(h.w.admin, "2026-10-09T23:30:00-04:00")
    inv = (await kabir.get(f"/api/v1/houses/{h.w.hid}/inventory")).json()
    for prefix in ("Greek yogurt", "Baby spinach"):  # Meera owns both; Pranav co-owns the spinach
        item = next(i for a in inv["areas"] for i in a["items"] if i["name"].startswith(prefix))
        assert (await kabir.post(f"/api/v1/inventory/{item['id']}/requests", {"amount": "a_bit"})).status_code == 201
    await drain(h.w.admin)
    await at(h.w.admin, "2026-10-10T07:59:00-04:00")
    await drain(h.w.admin)

    def asks(name: str) -> list[str]:
        return [x["title"] for x in h.phones[name].payloads() if "ask" in x["title"]]

    assert asks("meera") == [] and asks("pranav") == [], "held overnight"
    await at(h.w.admin, "2026-10-10T08:00:30-04:00")
    await drain(h.w.admin)
    assert asks("meera") == ["2 asks for your food"], "one grouped notification per type"
    assert asks("pranav") == ["Kabir asked for your baby spinach"]
    await drain(h.w.admin)
    assert len(asks("meera")) == 1, "delivered once"


@tid("PUSH-11")
async def test_PUSH_11_share_changes_coalesce_to_the_net_change(api: ApiFactory, fake_push: str) -> None:
    h, bill_id, ids = await household(api, fake_push)
    await drain(h.w.admin)
    coffee = ids[KEYS.index("coffee")]
    await tick(h.people["meera"], bill_id, coffee, h.w.key("meera"), True)
    await advance(h.w.admin, 120)
    await tick(h.people["meera"], bill_id, coffee, h.w.key("meera"), False)
    await advance(h.w.admin, 120)
    await tick(h.people["arjun"], bill_id, coffee, h.w.key("arjun"), True)
    await advance(h.w.admin, 601)
    await drain(h.w.admin)
    shares = [x for x in h.phones["pranav"].payloads() if x["title"].endswith("share changed")]
    assert len(shares) == 1, "one push per bill per member per 10 minutes"
    assert shares[0]["body"] == "You're now at $21.82 (−$1.69).", "the net change, latest share"
    # A change that nets to zero is never sent.
    await tick(h.people["meera"], bill_id, ids[KEYS.index("bars")], h.w.key("meera"), True)
    await tick(h.people["meera"], bill_id, ids[KEYS.index("bars")], h.w.key("meera"), False)
    await advance(h.w.admin, 601)
    await drain(h.w.admin)
    assert len([x for x in h.phones["arjun"].payloads() if x["title"].endswith("share changed")]) == 0


@tid("PUSH-12")
async def test_PUSH_12_daily_cap_of_20_then_inbox_only(
    api: ApiFactory, fake_push: str, app: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from app.services.notify import Message, create_notifications

    me = api()
    await me.sign_in(f"busy+{uuid7()}@example.com")
    await at(me, DAYTIME)
    phone = Phone(fake_push, f"busy-{uuid7()}")
    await phone.register(me)
    now = app.state.clock.now()
    async with sessions() as db, db.begin():
        for n in range(25):
            msg = Message(uuid.UUID(me.user_id), "pay", f"Payment {n}", "Tap to confirm.", "/", f"pay-{n}")
            await create_notifications(db, [msg], event_id=f"cap:{n}", house_id=None, seq=None, now=now)
    await drain(me)
    assert len(phone.payloads()) == 20
    async with sessions() as db:
        capped = await db.scalar(
            select(func.count()).select_from(Notification).where(Notification.last_error == "daily_cap")
        )
    assert capped == 5
    assert (await me.get("/api/v1/me/inbox")).json()["unread"] == 25, "the overflow is still in the inbox"


@tid("PUSH-13")
async def test_PUSH_13_retried_drain_never_duplicates(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    h, _, _ = await household(api, fake_push)
    async with sessions() as db:
        rows = (await db.scalars(select(OutboxRow))).all()
    await drain(h.w.admin)
    async with sessions() as db:
        first = await db.scalar(select(func.count()).select_from(Notification).where(Notification.type == "tab"))
    assert first == 9
    async with sessions() as db, db.begin():  # the worker died after sending, before marking done
        await db.execute(update(OutboxRow).where(OutboxRow.id.in_([r.id for r in rows])).values(status="pending"))
    await drain(h.w.admin)
    async with sessions() as db:
        n = await db.scalar(select(func.count()).select_from(Notification).where(Notification.type == "tab"))
    assert n == 9, "3 members x (inbox, push, email), once each"
    assert len(h.phones["arjun"].payloads()) == 1


@tid("PUSH-14")
async def test_PUSH_14_rolled_back_change_notifies_nobody(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.house_write import HouseWrite

    h, bill_id, _ = await household(api, fake_push)
    await drain(h.w.admin)
    async with sessions() as db:
        before = await db.scalar(select(func.count()).select_from(OutboxRow))
    original = HouseWrite._finish

    async def finish_then_fail(self: HouseWrite, clock: Any) -> None:
        await original(self, clock)  # events, activity and outbox rows are written...
        raise RuntimeError("the commit never happens")  # ...then the transaction rolls back

    monkeypatch.setattr(HouseWrite, "_finish", finish_then_fail)
    with pytest.raises(RuntimeError):
        await h.w.admin.post(f"/api/v1/bills/{bill_id}/remind", {"member_id": h.w.member["arjun"]})
    monkeypatch.setattr(HouseWrite, "_finish", original)
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(OutboxRow)) == before
    await drain(h.w.admin)
    assert not any(x["title"].startswith("Quick nudge") for x in h.phones["arjun"].payloads())


@tid("PUSH-05")
async def test_PUSH_05_gone_deletes_and_five_failures_mark_stale(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    me = api()
    await me.sign_in(f"flaky+{uuid7()}@example.com")
    await at(me, DAYTIME)
    gone, flaky = Phone(fake_push, f"gone-{uuid7()}"), Phone(fake_push, f"flaky-{uuid7()}")
    await gone.register(me)
    await flaky.register(me)
    httpx.post(f"{fake_push}/__control/respond", json={"status": 410, "sub_id": gone.sub_id, "times": 1})
    httpx.post(f"{fake_push}/__control/respond", json={"status": 500, "sub_id": flaky.sub_id, "times": 5})
    try:
        for _ in range(5):
            await me.post("/api/v1/push/test", idem=False)
            await drain(me)
    finally:
        httpx.delete(f"{fake_push}/__control/respond")
    devices = (await me.get("/api/v1/push/subscriptions")).json()
    assert [d["stale"] for d in devices] == [True], "410 removed one device; five failures marked the other stale"


@tid("PUSH-17")
async def test_PUSH_17_test_push_reaches_every_device(api: ApiFactory, fake_push: str) -> None:
    me = api()
    await me.sign_in(f"tester+{uuid7()}@example.com")
    await at(me, "2026-10-09T23:30:00-04:00")  # a test ignores quiet hours: the user just asked
    label = f"tester-{uuid7()}"
    phone, laptop = Phone(fake_push, label), Phone(fake_push, label)
    await phone.register(me)
    await laptop.register(me)
    assert (await me.post("/api/v1/push/test", idem=False)).status_code == 202
    await drain(me)
    got = phone.received()
    assert len(got) == 2 and {g["sub_id"] for g in got} == {phone.sub_id, laptop.sub_id}
    assert got[0]["payload"]["title"] == "It works!"


@tid("PUSH-16")
async def test_PUSH_16_badge_counts_unseen_and_clears_when_seen(api: ApiFactory, fake_push: str) -> None:
    h, bill_id, ids = await household(api, fake_push)
    await drain(h.w.admin)
    kabir = h.people["kabir"]
    inv = (await kabir.get(f"/api/v1/houses/{h.w.hid}/inventory")).json()
    chicken = next(i for a in inv["areas"] for i in a["items"] if i["name"].startswith("Chicken"))
    ask = await kabir.post(f"/api/v1/inventory/{chicken['id']}/requests", {"amount": "a_bit"})
    await tick(h.people["meera"], bill_id, ids[KEYS.index("coffee")], h.w.key("meera"), True)
    await drain(h.w.admin)
    box = (await h.w.admin.get("/api/v1/me/inbox")).json()
    assert box["badge"] == 2, "one pending ask + one unseen share change"
    after = await h.w.admin.post("/api/v1/me/inbox/read", {"types": ["asks"], "all": True}, idem=False)
    assert after.json()["badge"] == 2, "reading the inbox doesn't answer the ask: it still waits on me"
    # A co-owner answers it: it leaves my badge at once, without my reading anything.
    await h.people["arjun"].post(f"/api/v1/requests/{ask.json()['id']}/answer", {"decision": "free"})
    assert (await h.w.admin.get("/api/v1/me/inbox")).json()["badge"] == 1
    after = await h.w.admin.post("/api/v1/me/inbox/read", {"url": f"/bills/{bill_id}"}, idem=False)
    assert after.json()["badge"] == 0


@tid("KITCH-15")
@fr("FR-78")
async def test_KITCH_15_use_soon_round_up_at_9_once_per_state(api: ApiFactory, fake_push: str) -> None:
    h, _, _ = await household(api, fake_push)
    await drain(h.w.admin)
    phone = h.phones["meera"]
    await at(h.w.admin, "2026-10-06T07:00:00-04:00")  # spinach reaches 80% today (5-day shelf life)
    assert (await h.w.admin.post("/__test/use-soon", idem=False)).json()["created"] > 0
    await drain(h.w.admin)
    assert not [x for x in phone.payloads() if x["tag"].startswith("soon-")], "not before 9:00"
    await at(h.w.admin, "2026-10-06T09:00:30-04:00")
    await drain(h.w.admin)
    [soon] = [x for x in phone.payloads() if x["tag"].startswith("soon-")]
    assert (soon["title"], soon["body"]) == ("Baby spinach needs eating", "Yours and Pranav's. About a day left.")
    await h.w.admin.post("/__test/use-soon", idem=False)  # cron re-run, same state: nothing new
    await at(h.w.admin, "2026-10-06T10:00:00-04:00")
    await h.w.admin.post("/__test/use-soon", idem=False)
    await drain(h.w.admin)
    assert len([x for x in phone.payloads() if x["tag"].startswith("soon-")]) == 1
    await at(h.w.admin, "2026-10-07T07:00:00-04:00")  # next state: "Still there?"
    await h.w.admin.post("/__test/use-soon", idem=False)
    await at(h.w.admin, "2026-10-07T09:01:00-04:00")
    await drain(h.w.admin)
    assert len([x for x in phone.payloads() if x["tag"].startswith("soon-")]) == 2


async def test_cron_requires_the_secret(api: ApiFactory) -> None:
    anon = api()
    assert (await anon.get("/api/v1/cron/daily")).status_code == 401
    wrong = await anon.get("/api/v1/cron/daily", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    ok = await anon.get("/api/v1/cron/daily", headers={"Authorization": "Bearer test-cron-secret-0123456789abcdef01"})
    assert ok.status_code == 200 and ok.json()["failed"] == []
    assert set(ok.json()) == {"expired_asks", "purged", "soon", "digests", "auto_reminders", "sent", "backup", "failed"}


@tid("SEC-10")
async def test_SEC_10_push_subscriptions_only_reachable_by_their_owner(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    a, b = api(), api()
    await a.sign_in(f"a+{uuid7()}@example.com")
    await b.sign_in(f"b+{uuid7()}@example.com")
    phone = Phone(fake_push, f"shared-{uuid7()}")
    sub = await phone.register(a)
    assert (await b.call("DELETE", f"/api/v1/push/subscriptions/{sub['id']}")).status_code == 403
    assert (await b.get("/api/v1/push/subscriptions")).json() == [], "b never sees a's devices"
    missing = await b.call("DELETE", f"/api/v1/push/subscriptions/{uuid7()}")
    assert missing.status_code == 404
    async with sessions() as db:
        assert await db.get(PushSubscription, uuid.UUID(sub["id"])) is not None, "b can't remove a's device"


@tid("EMAIL-01")
async def test_EMAIL_01_verified_only_with_list_unsubscribe_and_footer(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from app.models import User

    h, _, _ = await household(api, fake_push, send=False)
    meera, kabir = h.people["meera"], h.people["kabir"]
    async with sessions() as db, db.begin():  # Kabir's address is unverified (e.g. typed, never confirmed)
        user = await db.get(User, uuid.UUID(kabir.user_id or ""))
        assert user is not None
        user.email_verified_at = None
    bill = (await h.w.admin.get(f"/api/v1/houses/{h.w.hid}/tabs")).json()["drafts"][0]["id"]
    await h.w.admin.post(f"/api/v1/bills/{bill}/send")
    await drain(h.w.admin)
    [mail] = [m for m in await emails(meera, meera.email or "") if "split a ShopRite run" in m["subject"]]
    unsub = mail["headers"]["List-Unsubscribe"]
    assert unsub.startswith("<https://app.test/api/v1/email/unsubscribe?") and unsub.endswith(">")
    assert mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "KitchenTab, 1 Test Street, Testville" in mail["text"] and "Stop these emails" in mail["text"]
    assert "prefers-color-scheme: dark" in mail["html"], "EMAIL-05: the HTML follows the reader's mode"
    assert [m for m in await emails(kabir, kabir.email or "") if "split a ShopRite run" in m["subject"]] == []


@tid("EMAIL-02")
async def test_EMAIL_02_unsubscribe_turns_off_that_type_for_email_only(api: ApiFactory, fake_push: str) -> None:
    h, _, _ = await household(api, fake_push, send=False)
    meera = h.people["meera"]
    bill = (await h.w.admin.get(f"/api/v1/houses/{h.w.hid}/tabs")).json()["drafts"][0]["id"]
    await h.w.admin.post(f"/api/v1/bills/{bill}/send")
    await drain(h.w.admin)
    [mail] = [m for m in await emails(meera, meera.email or "") if "split a ShopRite run" in m["subject"]]
    link = mail["headers"]["List-Unsubscribe"].strip("<>").removeprefix("https://app.test")
    anon = api()
    page = await anon.get(link)
    assert page.status_code == 200 and "Stop emails about new tabs?" in page.text
    assert (await meera.get("/api/v1/me/notification-prefs")).json()["email"]["tab"] is True, "a GET changes nothing"
    done = await anon.http.post(link, content="List-Unsubscribe=One-Click")  # mail client, no session or CSRF
    assert done.status_code == 200 and "No more emails about new tabs" in done.text
    prefs = (await meera.get("/api/v1/me/notification-prefs")).json()
    assert prefs["email"]["tab"] is False and prefs["push"]["tab"] is True and prefs["email"]["pay"] is True
    forged = await anon.http.post(link.replace("t=tab", "t=pay"))
    assert forged.status_code == 404, "the signature covers the type"


@tid("EMAIL-03")
@fr("FR-78")
async def test_EMAIL_03_weekly_digest_sunday_10_only_when_opted_in(api: ApiFactory, fake_push: str) -> None:
    h, _, _ = await household(api, fake_push)
    meera, kabir = h.people["meera"], h.people["kabir"]
    await meera.call("PATCH", "/api/v1/me", {"notify_kitchen_digest": True})
    secret = {"Authorization": "Bearer test-cron-secret-0123456789abcdef01"}
    await at(h.w.admin, "2026-10-09T12:00:00-04:00")  # Friday: Sunday 10:00 is more than 36 h away
    assert (await h.w.admin.get("/api/v1/cron/daily", headers=secret)).json()["digests"] == 0
    await at(h.w.admin, "2026-10-10T12:00:00-04:00")  # Saturday's cron schedules it
    assert (await h.w.admin.get("/api/v1/cron/daily", headers=secret)).json()["digests"] == 1
    assert (await h.w.admin.get("/api/v1/cron/daily", headers=secret)).json()["digests"] == 0, "idempotent"
    await at(h.w.admin, "2026-10-11T09:59:00-04:00")
    await drain(h.w.admin)
    assert [m for m in await emails(meera, meera.email or "") if m["subject"].startswith("Use soon")] == []
    await at(h.w.admin, "2026-10-11T10:00:30-04:00")
    await drain(h.w.admin)
    [digest] = [m for m in await emails(meera, meera.email or "") if m["subject"].startswith("Use soon")]
    assert digest["subject"].startswith("Use soon at Apt 4B") and "Baby spinach" in digest["text"]
    assert [m for m in await emails(kabir, kabir.email or "") if m["subject"].startswith("Use soon")] == []
    assert not any("Use soon" in x["title"] for x in h.phones["meera"].payloads()), "email only"


@tid("VERCEL-04")
async def test_VERCEL_04_outbox_drained_through_the_queues_sdk(
    api: ApiFactory, fake_push: str, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real SDK against its embedded queue service: a commit sends `outbox-drain`, the push
    subscriber in app/queues/outbox.py drains, and the push arrives once."""
    import anyio
    from vercel.queue.embedded import embedded_queue_service

    import app.queues.outbox as subscriber
    from app.adapters.queue import VercelKicker

    h, _, _ = await household(api, fake_push, send=False)
    await drain(h.w.admin)  # the joins
    async with embedded_queue_service() as service:
        kick = VercelKicker(service.get_async_client())
        monkeypatch.setattr(app.state.writes, "kicker", kick)
        monkeypatch.setattr(subscriber, "_deps", lambda: app.state.notify)
        bill = (await h.w.admin.get(f"/api/v1/houses/{h.w.hid}/tabs")).json()["drafts"][0]["id"]
        assert (await h.w.admin.post(f"/api/v1/bills/{bill}/send")).status_code == 200
        for _ in range(100):  # the subscriber runs on the queue's dispatcher, not in this request
            if h.phones["arjun"].payloads():
                break
            await anyio.sleep(0.1)
    assert [x["title"] for x in h.phones["arjun"].payloads()] == ["Pranav split a ShopRite run"]


@tid("VERCEL-04", "PUSH-10")
async def test_VERCEL_04_held_notifications_schedule_a_delayed_wake_up(
    api: ApiFactory, fake_push: str, app: Any
) -> None:
    from app.adapters.queue import RecordingKicker

    h, bill_id, _ = await household(api, fake_push)
    await drain(h.w.admin)
    kicker = RecordingKicker()
    app.state.notify.schedule = lambda when: _record(kicker, when, app)
    await at(h.w.admin, "2026-10-09T23:30:00-04:00")
    await h.w.admin.post(f"/api/v1/bills/{bill_id}/remind", {"member_id": h.w.member["arjun"]})
    await drain(h.w.admin)
    assert kicker.calls[-1]["delay_seconds"] == 8 * 3600 + 30 * 60, "wake at 08:00 for the held reminder"


async def _record(kicker: Any, when: Any, app: Any) -> None:
    await kicker.kick(delay_seconds=(when - app.state.clock.now()).total_seconds())


@tid("VERCEL-09")
@fr("FR-76", "FR-78")
async def test_VERCEL_09_daily_cron_is_idempotent(api: ApiFactory, fake_push: str, app: Any) -> None:
    h, _, _ = await household(api, fake_push)
    secret = {"Authorization": "Bearer test-cron-secret-0123456789abcdef01"}
    inv = (await h.people["kabir"].get(f"/api/v1/houses/{h.w.hid}/inventory")).json()
    yogurt = next(i for a in inv["areas"] for i in a["items"] if i["name"].startswith("Greek yogurt"))
    await h.people["kabir"].post(f"/api/v1/inventory/{yogurt['id']}/requests", {"amount": "a_bit"})
    await at(h.w.admin, "2026-10-12T07:00:00-04:00")  # ask is 3 days old; yogurt is near its date
    first = (await h.w.admin.get("/api/v1/cron/daily", headers=secret)).json()
    second = (await h.w.admin.get("/api/v1/cron/daily", headers=secret)).json()
    assert first["expired_asks"] == 1 and first["soon"] > 0
    assert (second["expired_asks"], second["soon"], second["digests"]) == (0, 0, 0)
    # The daily logical export: one private file per day, overwritten by a re-run (BUILD_ALL C2).
    from app.services.backup import _read

    assert first["backup"]["name"] == second["backup"]["name"] == "backups/kitchentab-2026-10-12.jsonl.gz"
    storage = app.state.storage  # the adapter the cron used (MinIO locally, fake Blob with PROFILE=vercel)
    info = await storage.head(first["backup"]["name"])
    assert info is not None and info.size == second["backup"]["bytes"]
    header, rows = _read(await storage.get(first["backup"]["name"]))
    assert header["tables"]["share_requests"] == 1 and sum(1 for _ in rows) == second["backup"]["rows"]


@tid("PAY-08")
@fr("FR-41")
async def test_PAY_08_auto_reminders_on_day_3_and_7_when_opted_in(api: ApiFactory, fake_push: str) -> None:
    h, bill_id, _ = await household(api, fake_push)
    secret = {"Authorization": "Bearer test-cron-secret-0123456789abcdef01"}
    await drain(h.w.admin)

    async def cron_at(when: str) -> int:
        await at(h.w.admin, when)
        n = int((await h.w.admin.get("/api/v1/cron/daily", headers=secret)).json()["auto_reminders"])
        await drain(h.w.admin)
        return n

    def nudges(name: str) -> int:
        return sum(x["title"] == "Quick nudge from Pranav" for x in h.phones[name].payloads())

    assert await cron_at("2026-10-12T15:00:00-04:00") == 0, "not opted in: nothing automatic"
    assert (await h.w.admin.call("PATCH", "/api/v1/me", {"auto_remind": True})).json()["auto_remind"] is True
    await h.people["kabir"].post(
        f"/api/v1/bills/{bill_id}/payments", {"participant": h.w.key("kabir"), "amount_cents": 1889, "method": "cash"}
    )
    assert await cron_at("2026-10-12T15:00:00-04:00") == 3, "day 3: Arjun, Meera and Kabir (unconfirmed) owe"
    assert await cron_at("2026-10-13T15:00:00-04:00") == 0, "once per stage"
    payment = (await h.w.admin.get(f"/api/v1/bills/{bill_id}/money")).json()
    pending = [p for p in payment["payments"] if p["status"] == "pending_confirmation"]
    assert len(pending) == 1
    for p in pending:
        assert (await h.w.admin.post(f"/api/v1/payments/{p['id']}/confirm")).status_code == 200
    assert await cron_at("2026-10-16T15:00:00-04:00") == 2, "day 7: only those still owing"
    assert await cron_at("2026-10-20T15:00:00-04:00") == 0, "no stage after day 7"
    assert (nudges("arjun"), nudges("meera"), nudges("kabir"), nudges("pranav")) == (2, 2, 1, 0)


@tid("PUSH-18")
async def test_PUSH_18_emoji_heavy_text_stays_under_2kb_and_devices_stay_healthy(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    h, _, _ = await household(api, fake_push)
    await drain(h.w.admin)
    inv = (await h.people["kabir"].get(f"/api/v1/houses/{h.w.hid}/inventory")).json()
    yogurt = next(i for a in inv["areas"] for i in a["items"] if i["name"].startswith("Greek yogurt"))
    note = "\U0001f60b" * 140  # the longest note, all 4-byte emoji
    r = await h.people["kabir"].post(f"/api/v1/inventory/{yogurt['id']}/requests", {"amount": "all", "note": note})
    assert r.status_code == 201
    await drain(h.w.admin)
    [got] = [g for g in h.phones["meera"].received() if "asked for" in g["payload"]["title"]]
    assert got["plaintext_bytes"] <= 2048 and got["payload"]["body"].startswith("All of it, \U0001f60b")
    async with sessions() as db:
        stale = await db.scalar(
            select(func.count()).select_from(PushSubscription).where(PushSubscription.failure_count > 0)
        )
    assert stale == 0


@tid("PUSH-06")
@fr("FR-38", "FR-42", "FR-72")
async def test_PUSH_06_decline_reject_reopen_and_void_copy(api: ApiFactory, fake_push: str) -> None:
    h, bill_id, _ = await household(api, fake_push)
    w, p, ph = h.w, h.people, h.phones
    await w.admin.call("PUT", "/api/v1/me/notification-prefs", {"push": {"settled": True}})
    for name in NAMES:
        await p[name].call("PUT", "/api/v1/me/notification-prefs", {"push": {"settled": True}})
    await drain(w.admin)
    inv = (await p["kabir"].get(f"/api/v1/houses/{w.hid}/inventory")).json()
    yogurt = next(i for a in inv["areas"] for i in a["items"] if i["name"].startswith("Greek yogurt"))
    ask = await p["kabir"].post(f"/api/v1/inventory/{yogurt['id']}/requests", {"amount": "a_bit"})
    await p["meera"].post(f"/api/v1/requests/{ask.json()['id']}/answer", {"decision": "decline"})
    paid = await p["arjun"].post(
        f"/api/v1/bills/{bill_id}/payments", {"participant": w.key("arjun"), "amount_cents": 3391, "method": "zelle"}
    )
    await drain(w.admin)
    await w.admin.post(f"/api/v1/payments/{paid.json()['id']}/reject", {"note": "Didn't see it in Zelle yet"})
    await drain(w.admin)
    titles = {n: [(x["title"], x["body"]) for x in ph[n].payloads()] for n in NAMES}
    assert ("Meera can't share the greek yogurt", "Not this time.") in titles["kabir"]
    assert ("Pranav didn't get your $33.91", "Didn't see it in Zelle yet") in titles["arjun"]
    await w.admin.post(f"/api/v1/bills/{bill_id}/settle", {"confirm_outstanding": True})
    await drain(w.admin)
    await w.admin.post(f"/api/v1/bills/{bill_id}/reopen")
    await drain(w.admin)
    await w.admin.post(f"/api/v1/bills/{bill_id}/void", {"reason": "Wrong store"})
    await drain(w.admin)
    meera = [x["title"] for x in ph["meera"].payloads()]
    assert meera[-3:] == ["ShopRite is settled", "ShopRite was reopened", "ShopRite was cancelled"]
    assert ph["meera"].payloads()[-1]["body"] == "Wrong store"
    assert not any("ShopRite was" in x["title"] for x in ph["pranav"].payloads()), "never the actor"


@tid("KITCH-15")
@fr("FR-78")
async def test_KITCH_15_round_up_waits_for_9_in_the_house_time_zone(api: ApiFactory, fake_push: str) -> None:
    """A house in Berlin: the daily cron runs at 11:00 UTC (13:00 there), so the round-up is for the
    next morning; nothing shows in the app or on the phone before 9:00 Berlin time, and food used up
    meanwhile drops out."""
    admin = api()
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    await at(admin, "2026-10-02T09:00:00+00:00")
    await admin.call("PATCH", "/api/v1/me", {"timezone": "Europe/Berlin"})  # quiet hours in my own zone
    house = await admin.post("/api/v1/houses", {"name": "Altbau", "roommates": ["Meera"], "timezone": "Europe/Berlin"})
    hid = house.json()["house"]["id"]
    item = await admin.post(f"/api/v1/houses/{hid}/inventory", {"name": "Spinat", "shelf_life_days": 5})
    phone = Phone(fake_push, f"berlin-{uuid7()}")
    await phone.register(admin)
    await at(admin, "2026-10-05T11:00:00+00:00")  # cron day: Oct 5, 13:00 in Berlin; 80% falls on Oct 6
    assert (await admin.post("/__test/use-soon", idem=False)).json()["created"] > 0
    await drain(admin)
    inbox = (await admin.get("/api/v1/me/inbox")).json()["items"]
    assert not [x for x in inbox if x["type"] == "soon"] and not phone.payloads(), "not before 9:00 Berlin"
    await at(admin, "2026-10-06T06:59:00+00:00")  # 08:59 in Berlin
    await drain(admin)
    assert not phone.payloads()
    await at(admin, "2026-10-06T07:00:30+00:00")  # 09:00:30 in Berlin
    await drain(admin)
    assert [x["title"] for x in phone.payloads()] == ["Spinat needs eating"]
    assert [x["title"] for x in (await admin.get("/api/v1/me/inbox")).json()["items"] if x["type"] == "soon"] == [
        "Spinat needs eating"
    ]
    # Used up before the next round-up's 9:00: that one is dropped, in the app and on the phone.
    await admin.post(f"/api/v1/inventory/{item.json()['id']}/still-good")
    await at(admin, "2026-10-09T11:00:00+00:00")
    await admin.post("/__test/use-soon", idem=False)
    await admin.post(f"/api/v1/inventory/{item.json()['id']}/used-up")
    await at(admin, "2026-10-10T07:00:30+00:00")
    await drain(admin)
    assert len(phone.payloads()) == 1
