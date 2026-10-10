"""Chaos suite (TEST_PLAN 3.12; `make test-chaos`; ADR-0024): dependencies failing in the middle of
real work. CHAOS-01 and CHAOS-02 live with the sync tests (SYNC-16, SYNC-18).

* CHAOS-03 Postgres connection lost mid-write: (a) the API's backend is terminated from a second
  connection at every single database call of a write in turn (deterministic, normal suite);
  (b) under concurrent writes, Toxiproxy between the API and Postgres drops every connection
  ("restart"), resets connections and cuts responses mid-stream (marker `chaos`).
  Either the whole write committed or none of it; the event log is gapless; the invariant
  checker is clean; a retry with the same Idempotency-Key does the write exactly once.
* CHAOS-04 the worker dies mid-send: one notification row per recipient × channel (dedupe keys);
  a push the service accepted just before the crash is sent again on retry with the same tag,
  event id and Topic, so the device replaces it (PRD §20.2 accepts replacement by tag).
* CHAOS-05 the push service answers 500 and 429: retries at 1 m, 5 m, 30 m and 2 h, then failed;
  404 and 410 delete the subscription.
* CHAOS-07 the optional LLM extractor hangs: the 30 s timeout ends it, the bill is `failed` and
  manual entry and browser OCR still work; with no EXTRACTOR_API_KEY nothing waits on it.

Tests marked `chaos` need Docker (Toxiproxy, or a full 30 s wait) and run only when asked for
(`-m chaos`, this file named on the command line, or `make test-chaos`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.util import await_only

from app.adapters.extractor import PLUGIN_TIMEOUT_SECONDS, FakeExtractor
from app.ids import uuid7
from app.main import create_app
from app.models import Base, HouseEvent, Notification, OutboxRow, Payment, PushSubscription
from app.services import notify
from app.services.invariants import check_all
from app.settings import Settings
from fakeservices import fake_blob
from fakeservices.fake_push import create_app as create_fake_push
from tests.harness.api import BASE_URL, Api
from tests.harness.servers import free_port, serve
from tests.harness.tags import fr, tid
from tests.harness.toxiproxy import Proxy, Toxiproxy, upstream_for
from tests.test_bills import KEYS, World, draft_with_items, tick_like_fixture
from tests.test_notify import DAYTIME, Phone, advance, at, drain, household
from tests.test_receipts import new_draft, ocr_parts, upload_file
from tests.test_sync_api import open_bill, tick

ApiFactory = Callable[[], Api]


# ---- CHAOS-03 (a): the connection dies at every database call of a write in turn ------------------


class BackendKiller:
    """Terminates the Postgres backend of the API connection that is about to make the k-th database
    call (a statement or a COMMIT) of the next request, using a second connection. The API's
    connection dies exactly there: before that statement runs, or with the COMMIT unsent."""

    def __init__(self, engine: AsyncEngine, admin: AsyncEngine) -> None:
        self.engine = engine.sync_engine
        self.admin = admin
        self.calls: list[str] = []
        self.armed: int | None = None
        self.killed: str | None = None

    def __enter__(self) -> BackendKiller:
        event.listen(self.engine, "before_cursor_execute", self._statement)
        event.listen(self.engine, "commit", self._commit)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._statement)
        event.remove(self.engine, "commit", self._commit)

    def arm(self, index: int | None) -> None:
        self.calls, self.armed, self.killed = [], index, None

    def _statement(self, conn: Connection, _cursor: Any, statement: str, *_args: Any) -> None:
        self._call(conn, " ".join(statement.split())[:70])

    def _commit(self, conn: Connection) -> None:
        self._call(conn, "COMMIT")

    def _call(self, conn: Connection, label: str) -> None:
        index = len(self.calls)
        self.calls.append(label)
        if self.armed == index and self.killed is None:
            pid = int(conn.connection.driver_connection.get_server_pid())  # type: ignore[union-attr]
            await_only(self._terminate(pid))  # we are inside SQLAlchemy's greenlet: run it on the loop
            self.killed = label

    async def _terminate(self, pid: int) -> None:
        async with self.admin.connect() as c:
            assert await c.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            for _ in range(400):  # gone before the API sends its next byte
                if not await c.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}):
                    return
                await asyncio.sleep(0.005)
        raise AssertionError(f"backend {pid} did not exit")


async def fingerprint(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Row counts of every table plus the version and amount sums a guarded edit changes."""
    tables = [t.name for t in Base.metadata.sorted_tables]
    parts = [f"(SELECT count(*) FROM {t}) AS {t}" for t in tables]  # noqa: S608 - our own table names
    parts += [
        "(SELECT coalesce(sum(event_seq), 0) FROM houses) AS event_seq_sum",
        "(SELECT coalesce(sum(version), 0) FROM items) AS item_versions",
        "(SELECT coalesce(sum(line_total_cents), 0) FROM items) AS item_cents",
        "(SELECT coalesce(sum(version), 0) FROM bills) AS bill_versions",
    ]
    async with sessions() as db:
        row = (await db.execute(text("SELECT " + ", ".join(parts)))).mappings().one()
    return {k: int(v) for k, v in row.items() if k not in ("sessions", "rate_limits")}


def delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {k: after[k] - before[k] for k in after if after[k] != before[k]}


async def assert_clean(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as db:
        assert await check_all(db, datetime.now(UTC)) == []


@pytest.fixture
async def killer(app: Any, settings: Settings) -> AsyncIterator[BackendKiller]:
    admin = create_async_engine(settings.database_url, poolclass=NullPool)
    with BackendKiller(app.state.engine, admin) as k:
        yield k
    await admin.dispose()


def lenient(app: Any) -> ApiFactory:
    """Clients that get the API's 500 response instead of the exception (as a browser would)."""
    clients: list[Api] = []

    def make() -> Api:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        a = Api(httpx.AsyncClient(transport=transport, base_url=BASE_URL, follow_redirects=False))
        clients.append(a)
        return a

    return make


async def sweep(
    killer: BackendKiller,
    sessions: async_sessionmaker[AsyncSession],
    *,
    prepare: Callable[[], Awaitable[None]],
    write: Callable[[], Awaitable[httpx.Response]],
    after_kill: Callable[[httpx.Response], Awaitable[None]] | None = None,
) -> set[str]:
    """Measure one clean run of `write`, then repeat it once per database call with the connection
    killed at that call. Every attempt either changes nothing or exactly what the clean run did."""
    await prepare()
    before = await fingerprint(sessions)
    killer.arm(None)
    clean = await write()
    assert clean.status_code < 300, clean.text
    expected = delta(await fingerprint(sessions), before)
    assert expected, "the clean run changed nothing"
    calls = len(killer.calls)
    killed_at: set[str] = set()
    for k in range(calls):
        await prepare()
        before = await fingerprint(sessions)
        killer.arm(k)
        resp = await write()
        label = killer.killed or "(not reached)"  # read before disarming: arm() clears it
        killer.arm(None)
        changed = delta(await fingerprint(sessions), before)
        killed_at.add(label)
        if resp.status_code < 300:
            assert changed == expected, f"call {k} [{label}]: partial write {changed} != {expected}"
        else:
            assert resp.status_code >= 500, (k, label, resp.status_code, resp.text)
            assert changed == {}, f"call {k} [{label}]: a failed write left {changed}"
            if after_kill is not None:
                await after_kill(resp)
        await assert_clean(sessions)
    return killed_at


def _mid_write(killed_at: set[str]) -> None:
    """The sweep reached the inside of the write transaction: taking the sequence number (the house
    lock), writing the event, and the COMMIT. The event insert is either its own statement or the
    one-statement commit path (`WITH ev AS (INSERT INTO house_events ...`, ADR-0023 amendment)."""
    takes_number = ("UPDATE houses SET", "SELECT event_seq FROM houses")
    assert any(s.startswith(takes_number) for s in killed_at), killed_at
    assert "COMMIT" in killed_at
    assert any(s.startswith(("INSERT INTO house_events", "WITH ev AS (INSERT INTO house_events")) for s in killed_at), (
        killed_at
    )


@tid("CHAOS-03")
@fr("FR-32", "FR-62")
async def test_CHAOS_03_tick_with_the_connection_killed_at_every_call(
    app: Any, killer: BackendKiller, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    w, bill_id, ids, p = await open_bill(lenient(app))
    yogurt = ids[KEYS.index("yogurt")]  # Meera only; Kabir joins it (owners follow, FR-62)

    async def prepare() -> None:
        r = await tick(p["kabir"], bill_id, yogurt, w.key("kabir"), False)
        assert r.status_code == 200, r.text

    killed_at = await sweep(
        killer,
        sessions,
        prepare=prepare,
        write=lambda: tick(p["kabir"], bill_id, yogurt, w.key("kabir"), True),
    )
    _mid_write(killed_at)


@tid("CHAOS-03", "SYNC-16")
@fr("FR-36")
async def test_CHAOS_03_payment_killed_mid_write_then_retried_with_the_same_key(
    app: Any, killer: BackendKiller, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    _w, bill_id, _ids, p = await open_bill(lenient(app))
    key = {"value": str(uuid.uuid4())}
    used: list[str] = []

    async def prepare() -> None:
        key["value"] = str(uuid.uuid4())
        used.append(key["value"])

    async def pay() -> httpx.Response:
        return await p["arjun"].post(f"/api/v1/bills/{bill_id}/payments", {"amount_cents": 1}, key=key["value"])

    async def retry(_failed: httpx.Response) -> None:
        # Nothing committed under that key: the retry is a fresh write, done once; a second retry replays.
        again = await pay()
        assert again.status_code == 201 and "Idempotent-Replayed" not in again.headers, again.text
        replay = await pay()
        assert replay.json() == again.json() and replay.headers["Idempotent-Replayed"] == "true"

    async with sessions() as db:
        payments_before = await db.scalar(select(func.count()).select_from(Payment))
    killed_at = await sweep(killer, sessions, prepare=prepare, write=pay, after_kill=retry)
    _mid_write(killed_at)
    async with sessions() as db:
        keys = await db.scalar(
            text("SELECT count(*) FROM idempotency_keys WHERE status_code = 201 AND key = ANY(:keys)"),
            {"keys": used},
        )
        payments = await db.scalar(select(func.count()).select_from(Payment))
    assert keys == len(used), "every key completed exactly once"
    assert int(payments or 0) - int(payments_before or 0) == keys, "one payment per Idempotency-Key, however often sent"


@tid("CHAOS-03")
@fr("FR-33")
async def test_CHAOS_03_guarded_price_edit_killed_at_every_call(
    app: Any, killer: BackendKiller, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    w, bill_id, ids, _ = await open_bill(lenient(app))
    coffee = ids[KEYS.index("coffee")]
    state: dict[str, int] = {}

    async def prepare() -> None:
        view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
        item = next(i for i in view["items"] if i["id"] == coffee)
        state["version"], state["price"] = item["version"], item["line_total_cents"]

    async def edit() -> httpx.Response:
        price = 1000 if state["price"] == 999 else 999
        return await w.admin.call(
            "PATCH", f"/api/v1/bills/{bill_id}/items/{coffee}", {"line_total_cents": price}, if_match=state["version"]
        )

    _mid_write(await sweep(killer, sessions, prepare=prepare, write=edit))


@tid("CHAOS-03")
@fr("FR-61", "FR-62")
async def test_CHAOS_03_send_killed_at_every_call_stocks_the_kitchen_once(
    app: Any, killer: BackendKiller, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """Send is the biggest write (link, share snapshot, kitchen items and owners, outbox)."""
    admin = lenient(app)()
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    w = World(admin, await admin.create_house())
    drafts: list[str] = []
    for _ in range(2):  # one for the measured clean run, one for the sweep
        bill_id, ids = await draft_with_items(w)
        await tick_like_fixture(w, bill_id, ids)
        drafts.append(bill_id)
    current = {"bill": drafts[0]}

    async def prepare() -> None:
        state = (await admin.get(f"/api/v1/bills/{current['bill']}")).json()["bill"]["state"]
        if state != "draft":
            current["bill"] = drafts[1]

    async def send() -> httpx.Response:
        return await admin.post(f"/api/v1/bills/{current['bill']}/send")

    _mid_write(await sweep(killer, sessions, prepare=prepare, write=send))
    final = await send()
    assert final.status_code in (200, 409), final.text  # the last attempt may have been the clean one
    async with sessions() as db:
        sent = await db.scalar(text("SELECT count(*) FROM bills WHERE state = 'open' AND house_id = :h"), {"h": w.hid})
        stocked = await db.scalar(text("SELECT count(DISTINCT bill_id) FROM inventory_items"))
    assert sent == 2 and stocked == 2


# ---- CHAOS-03 (b): Toxiproxy between the API and Postgres under concurrent writes -------------------


@pytest.fixture(scope="module")
def toxiproxy() -> Iterator[Toxiproxy]:
    tp = Toxiproxy.connect()
    yield tp
    tp.reset()
    tp.close()


@pytest.fixture
def pg_proxy(toxiproxy: Toxiproxy, settings: Settings) -> Iterator[Proxy]:
    proxy = toxiproxy.proxy("kt_chaos_pg", listen_port=26400, upstream=upstream_for(settings.database_url))
    yield proxy
    proxy.delete()


@asynccontextmanager
async def running(cfg: Settings) -> AsyncIterator[Any]:
    from fakeservices.mock_oidc import create_app as create_mock_oidc

    application = create_app(cfg)
    application.state.google_transport = httpx.ASGITransport(app=create_mock_oidc(cfg.google_issuer))
    async with application.router.lifespan_context(application):
        yield application


@pytest.mark.chaos
@tid("CHAOS-03")
@fr("FR-32", "FR-36", "FR-62")
async def test_CHAOS_03_postgres_restart_and_cut_connections_under_concurrent_writes(
    pg_proxy: Proxy, settings: Settings, sessions: async_sessionmaker[AsyncSession], db_reset: None
) -> None:
    cfg = settings.model_copy(update={"database_url": pg_proxy.url_for(settings.database_url)})
    async with running(cfg) as application:
        w, bill_id, ids, people = await open_bill(lenient(application))
        everyone = {"pranav": w.admin, **people}
        stop = asyncio.Event()
        ticks: list[tuple[str, int, bool]] = []  # (client mutation id, status, applied)
        payments: list[tuple[str, str, int]] = []  # (who, key, status)
        failures = 0

        async def call(fn: Callable[[], Awaitable[httpx.Response]]) -> int:
            nonlocal failures
            try:
                resp = await asyncio.wait_for(fn(), 30)
            except (TimeoutError, httpx.HTTPError):
                failures += 1
                return 599
            if resp.status_code >= 500:
                failures += 1
            return resp.status_code

        async def ticker(name: str, seed: int) -> None:
            rng = random.Random(seed)
            who = everyone[name]
            while not stop.is_set():
                cmid = str(uuid.uuid4())
                item, on = rng.choice(ids), rng.random() < 0.6
                result: dict[str, Any] = {}

                async def send(item: str = item, on: bool = on, cmid: str = cmid) -> httpx.Response:
                    resp = await who.call(
                        "PUT",
                        f"/api/v1/bills/{bill_id}/allocations",
                        {"changes": [{"item_id": item, "participant": w.key(name), "on": on}]},
                        headers={"X-Client-Mutation-Id": cmid},
                    )
                    if resp.status_code == 200:
                        result["applied"] = bool(resp.json()["applied"])
                    return resp

                status = await call(send)
                ticks.append((cmid, status, bool(result.get("applied"))))
                await asyncio.sleep(rng.uniform(0, 0.03))

        async def payer(name: str, seed: int) -> None:
            rng = random.Random(seed)
            who = people[name]
            while not stop.is_set():
                key = str(uuid.uuid4())

                async def pay(key: str = key) -> httpx.Response:
                    return await who.post(f"/api/v1/bills/{bill_id}/payments", {"amount_cents": 1}, key=key)

                status = await call(pay)
                payments.append((name, key, status))
                await asyncio.sleep(rng.uniform(0.02, 0.1))

        workers = [asyncio.create_task(ticker(n, i)) for i, n in enumerate(everyone)]
        workers += [asyncio.create_task(payer(n, 100 + i)) for i, n in enumerate(people)]
        await asyncio.sleep(1.5)
        pg_proxy.disable()  # Postgres restarts: every connection drops, new ones are refused
        await asyncio.sleep(2.0)
        pg_proxy.enable()
        await asyncio.sleep(1.5)
        # Connections reset by peer in the middle of transactions (half of them).
        pg_proxy.add_toxic("reset_peer", stream="upstream", toxicity=0.5, attributes={"timeout": 0})
        await asyncio.sleep(1.5)
        pg_proxy.heal()
        await asyncio.sleep(1.0)
        # Responses from Postgres cut after a few kilobytes: some die after COMMIT was sent.
        pg_proxy.add_toxic("limit_data", stream="downstream", toxicity=0.5, attributes={"bytes": 4000})
        await asyncio.sleep(1.5)
        pg_proxy.heal()
        await asyncio.sleep(1.5)
        stop.set()
        await asyncio.gather(*workers)
        assert failures > 0, "the chaos never reached a request"

        # The API recovered: a write goes through once Postgres is back.
        r = await tick(w.admin, bill_id, ids[0], w.key("pranav"), True)
        assert r.status_code == 200, r.text

        # Every payment key: retried until it lands; a key whose first try committed replays it.
        for name, key, status in payments:
            if status in (201, 422):
                continue
            again = await people[name].post(f"/api/v1/bills/{bill_id}/payments", {"amount_cents": 1}, key=key)
            assert again.status_code in (201, 422), again.text

    async with sessions() as db:
        events = (await db.execute(select(HouseEvent.client_mutation_id, HouseEvent.type))).all()
        landed_keys = await db.scalar(  # this test's payment keys only (setup creates also store 201s)
            text("SELECT count(*) FROM idempotency_keys WHERE status_code = 201 AND key = ANY(:keys)"),
            {"keys": [key for _, key, _ in payments]},
        )
        n_payments = await db.scalar(select(func.count()).select_from(Payment))
        assert await check_all(db, datetime.now(UTC)) == []
    per_cmid: dict[str, int] = {}
    for cmid, _type in events:
        if cmid:
            per_cmid[cmid] = per_cmid.get(cmid, 0) + 1
    assert all(n == 1 for n in per_cmid.values()), "one event per write"
    for cmid, status, applied in ticks:
        if status == 200:
            assert per_cmid.get(cmid, 0) == (1 if applied else 0), f"acknowledged tick {cmid} not in the log"
        elif status >= 500:
            assert per_cmid.get(cmid, 0) in (0, 1)
    assert n_payments == landed_keys, "one payment per key, never two"
    acknowledged = sum(1 for _, s, a in ticks if s == 200 and a)
    lost = sum(1 for _, s, _ in ticks if s >= 500)
    print(f"\nCHAOS-03 toxiproxy: {len(ticks)} ticks ({acknowledged} applied, {lost} failed), {len(payments)} payments")


# ---- CHAOS-04: the worker dies mid-send -----------------------------------------------------------


@pytest.fixture(scope="module")
def fake_push() -> Iterator[str]:
    port = free_port()
    with serve(create_fake_push(f"http://127.0.0.1:{port}"), port) as url:
        yield url


class WorkerCrash(BaseException):
    """Not an Exception: nothing in the drain catches it, like the process being killed."""


class CrashAfterAccepted:
    """The push service accepted `after` messages; the worker dies before the drain commits."""

    def __init__(self, real: notify.PushSender, after: int) -> None:
        self.real = real
        self.after = after
        self.accepted = 0

    async def send(self, sub: Any, payload: dict[str, Any], **kw: Any) -> Any:
        result = await self.real.send(sub, payload, **kw)
        self.accepted += 1
        if self.accepted >= self.after:
            raise WorkerCrash("killed after the push service answered 201")
        return result


def delivered(fake_push: str) -> list[dict[str, Any]]:
    got: list[dict[str, Any]] = httpx.get(f"{fake_push}/__test/push").json()
    return [r for r in got if r["answered"] == 201]


@tid("CHAOS-04", "PUSH-13")
async def test_CHAOS_04_worker_crash_after_pushes_were_accepted(
    api: ApiFactory, app: Any, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The drain commits a batch of deliveries at once: a crash after two pushes were accepted rolls
    both back, so both are sent again. Each repeat carries the original's tag, event id and Topic:
    the device shows one notification (replacement by tag, PRD §20.2), never two."""
    h, _bill_id, _ = await household(api, fake_push)
    httpx.delete(f"{fake_push}/__test/push")
    deps: notify.NotifyDeps = app.state.notify
    assert deps.push is not None
    crashing = CrashAfterAccepted(deps.push, after=2)
    with pytest.raises(WorkerCrash):
        await notify.drain(dataclasses.replace(deps, push=crashing))
    before_crash = delivered(fake_push)
    assert len(before_crash) == 2
    async with sessions() as db:  # the crash rolled the deliveries back: pending, not sent
        pushed = await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.channel == "push", Notification.status != "pending")
        )
    assert pushed == 0

    await drain(h.w.admin)  # the restarted worker
    by_device: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in delivered(fake_push):
        by_device.setdefault((r["sub_id"], r["payload"]["tag"]), []).append(r)
    repeated = {k: rs for k, rs in by_device.items() if len(rs) > 1}
    assert set(repeated) == {(r["sub_id"], r["payload"]["tag"]) for r in before_crash}, "only the rolled-back sends"
    same = ("title", "body", "url", "tag", "event_id")
    for first, again in repeated.values():
        assert {k: first["payload"][k] for k in same} == {k: again["payload"][k] for k in same}
        assert first["topic"] and first["topic"] == again["topic"], "same Topic: the service replaces it"
    assert all(len(rs) <= 2 for rs in by_device.values())
    async with sessions() as db:
        rows = (await db.execute(select(Notification.dedupe_key, Notification.channel, Notification.status))).all()
    assert len({k for k, _, _ in rows}) == len(rows), "one notification row per recipient x channel"
    assert {s for _, c, s in rows if c == "push"} == {"sent"}


@tid("CHAOS-04")
async def test_CHAOS_04_worker_crash_mid_fan_out_retries_without_duplicates(
    api: ApiFactory, app: Any, fake_push: str, sessions: async_sessionmaker[AsyncSession], monkeypatch: Any
) -> None:
    h, _bill_id, _ = await household(api, fake_push)
    httpx.delete(f"{fake_push}/__test/push")
    real = notify.create_notifications
    created = {"n": 0}

    async def crash_after_insert(*args: Any, **kw: Any) -> list[uuid.UUID]:
        ids = await real(*args, **kw)
        created["n"] += len(ids)
        if created["n"]:
            raise WorkerCrash("killed after inserting notification rows, before the outbox row was marked done")
        return ids

    monkeypatch.setattr(notify, "create_notifications", crash_after_insert)
    with pytest.raises(WorkerCrash):
        await notify.drain(app.state.notify)
    monkeypatch.undo()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Notification)) == 0, "rolled back with the crash"
        assert await db.scalar(select(func.count()).select_from(OutboxRow).where(OutboxRow.status != "pending")) == 0
    await drain(h.w.admin)
    await drain(h.w.admin)
    async with sessions() as db:
        tab = await db.scalar(select(func.count()).select_from(Notification).where(Notification.type == "tab"))
        done = await db.scalar(select(func.count()).select_from(OutboxRow).where(OutboxRow.status == "done"))
        total = await db.scalar(select(func.count()).select_from(OutboxRow))
    assert tab == 9, "3 members x (inbox, push, email), once each"
    assert done == total
    tags = [(r["sub_id"], r["payload"]["tag"]) for r in delivered(fake_push)]
    assert len(tags) == len(set(tags)), "no device got the same notification twice"


# ---- CHAOS-05: the push service answers 500 / 429 / 404 / 410 -----------------------------------------


def force(fake_push: str, phone: Phone, *statuses: int) -> None:
    """The next pushes to `phone` answer `statuses` in order (rules are checked newest first)."""
    for status in reversed(statuses):
        httpx.post(f"{fake_push}/__control/respond", json={"status": status, "sub_id": phone.sub_id, "times": 1})


async def _push_row(sessions: async_sessionmaker[AsyncSession], user_id: str) -> Notification:
    async with sessions() as db:
        row = (
            await db.scalars(
                select(Notification).where(Notification.user_id == uuid.UUID(user_id), Notification.channel == "push")
            )
        ).one()
    return row


async def _signed_in_with_phone(api: ApiFactory, fake_push: str, label: str) -> tuple[Api, Phone]:
    me = api()
    await me.sign_in(f"{label}+{uuid7()}@example.com")
    await at(me, DAYTIME)
    phone = Phone(fake_push, f"{label}-{uuid7()}")
    await phone.register(me)
    return me, phone


@tid("CHAOS-05")
async def test_CHAOS_05_500_then_429_retries_at_1m_5m_30m_2h_then_fails(
    api: ApiFactory, app: Any, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    me, phone = await _signed_in_with_phone(api, fake_push, "flaky")
    assert me.user_id
    force(fake_push, phone, 500, 429, 500, 429, 500)
    try:
        assert (await me.post("/api/v1/push/test", idem=False)).status_code in (200, 201, 202)
        start = app.state.clock.now()
        await drain(me)
        attempts = [r["answered"] for r in phone.received()]
        assert attempts == [500]
        elapsed = 0
        for gap, answer in ((60, 429), (300, 500), (1800, 429), (7200, 500)):
            row = await _push_row(sessions, me.user_id)
            assert row.status == "pending" and row.next_attempt_at is not None
            assert (row.next_attempt_at - start).total_seconds() == elapsed + gap, "backoff from the last attempt"
            await advance(me, gap - 1)
            await drain(me)
            assert len(phone.received()) == len(attempts), f"nothing sent before the {gap} s retry is due"
            await advance(me, 1)
            await drain(me)
            attempts.append(answer)
            assert [r["answered"] for r in phone.received()] == attempts
            elapsed += gap
        row = await _push_row(sessions, me.user_id)
        assert (row.status, row.attempts, row.next_attempt_at, row.last_error) == ("failed", 5, None, "push_failed")
        await advance(me, 86400)
        await drain(me)
        assert len(phone.received()) == 5, "given up after 1 m, 5 m, 30 m, 2 h"
    finally:
        httpx.delete(f"{fake_push}/__control/respond")
    devices = (await me.get("/api/v1/push/subscriptions")).json()
    assert [d["stale"] for d in devices] == [True], "5 consecutive failures mark the device stale (PUSH-05)"


@tid("CHAOS-05")
async def test_CHAOS_05_recovers_on_the_third_attempt_with_one_delivery(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    me, phone = await _signed_in_with_phone(api, fake_push, "recovers")
    assert me.user_id
    force(fake_push, phone, 500, 429)
    try:
        await me.post("/api/v1/push/test", idem=False)
        await drain(me)
        await advance(me, 60)
        await drain(me)
        await advance(me, 300)
        await drain(me)
    finally:
        httpx.delete(f"{fake_push}/__control/respond")
    assert [r["answered"] for r in phone.received()] == [500, 429, 201]
    row = await _push_row(sessions, me.user_id)
    assert (row.status, row.attempts) == ("sent", 2)
    await advance(me, 7200)
    await drain(me)
    assert len(phone.received()) == 3, "delivered once; nothing more after success"
    devices = (await me.get("/api/v1/push/subscriptions")).json()
    assert [d["stale"] for d in devices] == [False], "a success resets the failure count"


@tid("CHAOS-05", "PUSH-05")
async def test_CHAOS_05_404_and_410_delete_the_subscription(
    api: ApiFactory, fake_push: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    me, gone_404 = await _signed_in_with_phone(api, fake_push, "gone")
    assert me.user_id
    gone_410 = Phone(fake_push, f"gone410-{uuid7()}")
    await gone_410.register(me)
    force(fake_push, gone_404, 404)
    force(fake_push, gone_410, 410)
    try:
        await me.post("/api/v1/push/test", idem=False)
        await drain(me)
    finally:
        httpx.delete(f"{fake_push}/__control/respond")
    assert (await me.get("/api/v1/push/subscriptions")).json() == []
    async with sessions() as db:
        left = await db.scalar(
            select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == uuid.UUID(me.user_id))
        )
    assert left == 0
    row = await _push_row(sessions, me.user_id)
    assert (row.status, row.last_error, row.next_attempt_at) == ("suppressed", "no_devices", None), "no retry"
    await advance(me, 3600)
    await drain(me)
    assert len(gone_404.received()) == 1 and len(gone_410.received()) == 1


# ---- CHAOS-07: the optional LLM extractor hangs ------------------------------------------------------


@pytest.fixture(scope="module")
def blob_url(settings: Settings) -> Iterator[str | None]:
    if settings.profile != "vercel":
        yield None  # PROFILE=local: the compose MinIO
        return
    port = free_port()
    with serve(fake_blob.create_app(f"http://127.0.0.1:{port}"), port) as url:
        yield url


@pytest.fixture
def receipt_settings(settings: Settings, blob_url: str | None) -> Settings:
    if blob_url is None:
        return settings
    return settings.model_copy(update={"storage_provider": "fakeblob", "fake_blob_url": blob_url})


@asynccontextmanager
async def hanging_plugin(cfg: Settings, tmp_path: Path) -> AsyncIterator[Api]:
    """EXTRACTOR_API_KEY set and the extractor (the fake, never a real model) hangs for an hour."""
    keyed = cfg.model_copy(update={"extractor_provider": "fake", "extractor_api_key": "test-only-key"})
    plugin = FakeExtractor(tmp_path, None, hang_seconds=3600)
    await plugin.set_mode("hang")
    async with running(keyed) as application:
        application.state.extractor = plugin
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=BASE_URL, timeout=120)
        try:
            yield Api(client)
        finally:
            await client.aclose()


async def _hang_then_manual(admin: Api, timeout: float) -> None:
    await admin.sign_in(f"pranav+{uuid7()}@example.com")
    w = World(admin, await admin.create_house())
    bill_id = await new_draft(w)
    started = time.monotonic()
    resp = await upload_file(admin, bill_id, "i02.jpg")
    took = time.monotonic() - started
    assert resp.status_code == 201, resp.text
    assert resp.json()["extraction"] == "failed" and resp.json()["bill"]["extraction_status"] == "failed"
    assert timeout <= took < timeout + 10, f"the request took {took:.1f} s; the timeout is {timeout} s"
    view = (await admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert view["items"] == [] and view["bill"]["extraction_status"] == "failed", "nothing invented"
    assert len((await admin.get(f"/api/v1/bills/{bill_id}/receipts")).json()) == 1, "the photo stays"
    manual = await admin.post(f"/api/v1/bills/{bill_id}/items", {"name": "Milk", "line_total_cents": 429})
    assert manual.status_code == 201, "manual entry offered and works"
    ocr = await admin.post(f"/api/v1/bills/{bill_id}/ocr", {"parts": ocr_parts("i02")})
    assert ocr.status_code == 200 and ocr.json()["extraction"] in ("done", "offered"), "browser OCR still works"


@tid("CHAOS-07", "EXTRACT-06")
@fr("FR-11", "FR-15")
async def test_CHAOS_07_hanging_extractor_times_out_and_offers_manual_entry(
    receipt_settings: Settings, tmp_path: Path, monkeypatch: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The same path as the full 30 s run below (marker `chaos`), with the timeout shortened to 2 s."""
    from app.services import receipts

    assert PLUGIN_TIMEOUT_SECONDS == 30 and vars(receipts)["PLUGIN_TIMEOUT_SECONDS"] == 30, "PRD §9: timeout 30 s"
    monkeypatch.setattr(receipts, "PLUGIN_TIMEOUT_SECONDS", 2)
    async with hanging_plugin(receipt_settings, tmp_path) as admin:
        await _hang_then_manual(admin, 2)
    async with sessions() as db:
        error = await db.scalar(text("SELECT error FROM receipt_extractions ORDER BY created_at LIMIT 1"))
    assert error == "plugin_error: TimeoutError"


@pytest.mark.chaos
@tid("CHAOS-07")
@fr("FR-11")
async def test_CHAOS_07_hanging_extractor_full_30_seconds(
    receipt_settings: Settings, tmp_path: Path, db_reset: None
) -> None:
    async with hanging_plugin(receipt_settings, tmp_path) as admin:
        await _hang_then_manual(admin, PLUGIN_TIMEOUT_SECONDS)


@tid("CHAOS-07", "EXTRACT-15")
@fr("FR-11")
async def test_CHAOS_07_without_a_key_nothing_waits_on_the_extractor(
    receipt_settings: Settings, db_reset: None
) -> None:
    """No EXTRACTOR_API_KEY (the default): text PDFs are parsed here and photos go to the browser's
    OCR at once, even with the fake extractor told to hang."""
    assert receipt_settings.extractor_api_key is None
    async with running(receipt_settings) as application:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=BASE_URL, timeout=60)
        admin = Api(client)
        try:
            await admin.sign_in(f"pranav+{uuid7()}@example.com")
            assert (await admin.post("/__test/extractor", {"mode": "hang"}, idem=False)).status_code == 204
            w = World(admin, await admin.create_house())
            bill_id = await new_draft(w)
            started = time.monotonic()
            pdf = await upload_file(admin, bill_id, "r01.pdf")
            photo = await upload_file(admin, bill_id, "i02.jpg")
            took = time.monotonic() - started
        finally:
            await admin.post("/__test/extractor", {"mode": "normal"}, idem=False)
            await client.aclose()
    assert pdf.json()["extraction"] == "done", pdf.text
    assert photo.json()["extraction"] == "needs_ocr", "the photo goes to the browser's OCR at once"
    assert took < 15, f"{took:.1f} s: the local path never waited on the extractor"
