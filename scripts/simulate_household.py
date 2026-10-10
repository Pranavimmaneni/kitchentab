"""Live household simulation (BUILD_ALL §4.3; `make simulate SEED=1 MINUTES=30`).

Four roommates on six devices hammer one house with realistic actions at once: manual and PDF
bills, ticks, price edits, sends, payments (double taps and lost responses), confirmations,
settles, reopens, asks answered every way, used up, sharing switches, a roommate removed and
re-added, network drops and server restarts, while every device holds a live event stream that
rolls over and reconnects. Every 30 s (and at the end) everything pauses and the checks run:

    invariants   Σ shares = total, stored shares = computed, owners = allocations, balances net to
                 zero, gapless event sequence, notifications point at committed events
    streams      every device saw every event exactly once, in order, up to the latest
    views        every device's fresh GET of each open tab equals everyone else's
    push         no device received the same event twice
    errors       no 5xx outside a deliberate restart

Targets:
    --target local          starts its own API (4 workers) on a fresh database, the fake push service
                            and a drain loop (default)
    --target local-vercel   runs against the rehearsal: pass --base-url, --database-url (the direct
                            URL for the invariant checks) and --inbox-secret

Browser devices (three of the six) are added by `--browsers` once the web journeys exist; without
it all six devices are API clients with their own sessions and streams.

    uv run --project apps/api python scripts/simulate_household.py --seed 1 --minutes 30
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import random
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
API_DIR = REPO / "apps" / "api"
sys.path.insert(0, str(API_DIR))

from app.db import make_engine, make_sessionmaker
from app.services.invariants import check_all
from app.settings import asyncpg_url
from tests.harness.api import Api
from tests.harness.multiserver import (
    server_cmd,
    spawn_tree,
    stop_tree,
    wait_workers_ready,
)
from tests.harness.servers import free_port, serve

FIXTURES = REPO / "packages" / "fixtures" / "receipts"
INBOX_SECRET = "s" * 64
OK_CONFLICTS = {
    400,
    404,
    409,
    410,
    412,
    422,
    428,
    429,
}  # races and business rules: expected answers
NAMES = ("pranav", "arjun", "meera", "kabir")
DEVICES = (
    ("pranav", "phone"),
    ("pranav", "laptop"),
    ("arjun", "phone"),
    ("meera", "phone"),
    ("meera", "laptop"),
    ("kabir", "phone"),
)


# ---- the world --------------------------------------------------------------------------------------


@dataclass
class Stats:
    actions: Counter[str] = field(default_factory=Counter)
    statuses: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    checkpoints: int = 0
    restarts: int = 0
    reconnects: int = 0
    stream_endings: Counter[str] = field(
        default_factory=Counter
    )  # why streams reconnected

    def record(
        self, action: str, resp: httpx.Response | None, *, restarting: bool
    ) -> None:
        self.actions[action] += 1
        if resp is None:
            self.statuses["network"] += 1
            return
        self.statuses[str(resp.status_code)] += 1
        if resp.status_code >= 500 and not restarting:
            self.errors.append(f"{action}: {resp.status_code} {resp.text[:200]}")


class Stream:
    """A device's event stream: reconnects with Last-Event-ID after the 270 s rollover, a dropped
    connection or a server restart, and records every sequence number it was given."""

    def __init__(self, api: Api, house_id: str, stats: Stats) -> None:
        self.api, self.house_id, self.stats = api, house_id, stats
        self.seqs: list[int] = []
        self.last: int | None = None
        self.resyncs = 0
        self.skipped: set[int] = (
            set()
        )  # covered by a resync (the device refetched instead)
        self.asleep = False
        self._task: asyncio.Task[None] | None = None
        self._stop = False

    def start(self) -> None:
        self._stop = False
        self.asleep = False
        self._task = asyncio.create_task(self._run())

    async def sleep(self) -> None:
        """The phone locks or the tab is backgrounded: the connection goes away."""
        await self.stop()
        self.asleep = True

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _run(self) -> None:
        while not self._stop:
            headers = {"Accept": "text/event-stream"}
            if self.last is not None:
                headers["Last-Event-ID"] = str(self.last)
            try:
                async with self.api.http.stream(
                    "GET",
                    f"/api/v1/houses/{self.house_id}/events",
                    headers=headers,
                    timeout=None,
                ) as resp:
                    if resp.status_code != 200:
                        self.stats.stream_endings[f"status {resp.status_code}"] += 1
                        await asyncio.sleep(0.5)
                        continue
                    block: dict[str, str] = {}
                    async for line in resp.aiter_lines():
                        if line == "":
                            self._handle(block)
                            block = {}
                        elif not line.startswith((":", "retry:")):
                            key, _, value = line.partition(":")
                            block[key] = value.lstrip(" ")
                    self.stats.stream_endings["closed by server"] += 1
            except (httpx.HTTPError, OSError) as exc:
                self.stats.stream_endings[type(exc).__name__] += 1
            if not self._stop:
                self.stats.reconnects += 1
                await asyncio.sleep(0.2)

    def _handle(self, block: dict[str, str]) -> None:
        if "data" not in block:
            return
        event = block.get("event")
        data = json.loads(block["data"])
        if event in ("rollover", "revoked"):
            self.stats.stream_endings[f"{event} {data.get('reason', '')}".strip()] += 1
        if (
            event == "resync"
        ):  # too far behind: the device refetches, the stream goes on from `seq`
            self.resyncs += 1
            seq = int(data.get("seq", self.last or 0))
            self.skipped.update(range((self.last or seq) + 1, seq + 1))
            self.last = seq
            return
        if event is None and "seq" in data:
            seq = int(data["seq"])
            self.seqs.append(seq)
            self.last = seq


@dataclass
class Device:
    member: str
    kind: str
    api: Api
    stream: Stream | None = None

    @property
    def name(self) -> str:
        return f"{self.member}-{self.kind}"


@dataclass
class World:
    base_url: str
    house_id: str
    invite: str
    member_ids: dict[str, str]
    devices: list[Device]
    stats: Stats
    rng: random.Random
    restarting: bool = False
    leaver: str | None = None  # removed and re-added during the run, so never a payer
    paused: asyncio.Event = field(default_factory=asyncio.Event)
    in_flight: int = 0
    # Held by things that must not straddle a checkpoint: a restart, Kabir between removal and rejoin.
    quiet: asyncio.Lock = field(default_factory=asyncio.Lock)

    def key(self, name: str) -> str:
        return f"m:{self.member_ids[name]}"

    def device(self, member: str) -> Device:
        return self.rng.choice([d for d in self.devices if d.member == member])


# ---- requests ------------------------------------------------------------------------------------


async def call(
    w: World, d: Device, action: str, method: str, url: str, body: Any = None, **kw: Any
) -> httpx.Response | None:
    """One request as a phone would send it: sometimes the response is lost (the device retries the
    same request with the same Idempotency-Key), sometimes a nervous double tap sends it twice."""
    await w.paused.wait()
    w.in_flight += 1
    try:
        key = str(uuid.uuid4()) if method == "POST" else None
        fault = w.rng.random()
        try:
            if (
                fault < 0.05 and method == "POST"
            ):  # double tap: the same request twice at once
                a, b = await asyncio.gather(
                    d.api.call(method, url, body, key=key, **kw),
                    d.api.call(method, url, body, key=key, **kw),
                )
                if {a.status_code, b.status_code} - {200, 201, *OK_CONFLICTS}:
                    w.stats.errors.append(
                        f"{action} double tap: {a.status_code}/{b.status_code}"
                    )
                resp = a
            elif (
                fault < 0.10
            ):  # the response is lost on the way back: retry like the client does
                with contextlib.suppress(TimeoutError, httpx.TimeoutException):
                    await asyncio.wait_for(
                        d.api.call(method, url, body, key=key, **kw), timeout=0.01
                    )
                resp = await d.api.call(method, url, body, key=key, **kw)
            else:
                resp = await d.api.call(method, url, body, key=key, **kw)
        except (httpx.HTTPError, OSError, TimeoutError):
            resp = None
        w.stats.record(action, resp, restarting=w.restarting)
        return resp
    finally:
        w.in_flight -= 1


async def get(w: World, d: Device, url: str, **kw: Any) -> Any:
    try:
        r = await d.api.get(url, **kw)
    except (httpx.HTTPError, OSError):
        return None
    if (
        r.status_code >= 500 and not w.restarting
    ):  # a read must never fail (a torn view did, SYNC-35)
        w.stats.errors.append(
            f"GET {url.split('?')[0]}: {r.status_code} {r.text[:200]}"
        )
    return r.json() if r.status_code == 200 else None


async def drain(w: World) -> None:
    """What the queue does after every write; a failed drain re-sends what it already sent (PUSH-19)."""
    d = w.rng.choice(
        w.devices
    )  # any instance, often two at once: queue consumers run in parallel
    try:
        r = await d.api.post("/__test/drain", idem=False)
    except httpx.HTTPError:
        return
    if r.status_code >= 500 and not w.restarting:
        w.stats.errors.append(f"drain: {r.status_code} {r.text[:200]}")


# ---- actions -------------------------------------------------------------------------------------


async def open_tabs(w: World, d: Device) -> list[dict[str, Any]]:
    tabs = await get(w, d, f"/api/v1/houses/{w.house_id}/tabs")
    return list(tabs["open"]) if tabs else []


async def act_manual_bill(w: World, d: Device) -> None:
    if d.member == w.leaver:
        return
    r = await call(
        w,
        d,
        "create bill",
        "POST",
        f"/api/v1/houses/{w.house_id}/bills",
        {
            "store_name": w.rng.choice(
                ["ShopRite", "H Mart", "Costco", "Trader Joe's"]
            ),
            "purchase_date": "2026-10-02",
        },
    )
    if r is None or r.status_code != 201:
        return
    bill = r.json()["id"]
    items = []
    for n in range(w.rng.randint(3, 8)):
        cents = w.rng.randint(99, 2499)
        storage = w.rng.choice(["fridge", "pantry", "freezer", "none"])
        resp = await call(
            w,
            d,
            "add item",
            "POST",
            f"/api/v1/bills/{bill}/items",
            {
                "name": f"Item {n} {uuid.uuid4().hex[:4]}",
                "line_total_cents": cents,
                "storage": storage,
                "category": "other",
            },
        )
        if resp is not None and resp.status_code == 201:
            items.append(resp.json()["id"])
    changes = [
        {"item_id": i, "participant": w.key(m), "on": True}
        for i in items
        for m in w.rng.sample(NAMES, w.rng.randint(1, 4))
    ]
    await call(
        w,
        d,
        "tick (draft)",
        "PUT",
        f"/api/v1/bills/{bill}/allocations",
        {"changes": changes},
    )
    view = await get(w, d, f"/api/v1/bills/{bill}")
    if (
        view
        and view["reconciliation"].get("ok") is False
        and view["bill"].get("receipt_total_cents")
    ):
        await call(
            w,
            d,
            "override",
            "POST",
            f"/api/v1/bills/{bill}/reconciliation/override",
            {"reason": "sim"},
        )
    await call(w, d, "send", "POST", f"/api/v1/bills/{bill}/send")


async def act_pdf_bill(w: World, d: Device) -> None:
    if d.member == w.leaver:
        return
    r = await call(
        w, d, "create bill (pdf)", "POST", f"/api/v1/houses/{w.house_id}/bills", {}
    )
    if r is None or r.status_code != 201:
        return
    bill = r.json()["id"]
    if not await put_receipt(
        w, d, bill, w.rng.choice([f"r{n:02d}.pdf" for n in range(1, 16)])
    ):
        return
    view = await get(w, d, f"/api/v1/bills/{bill}")
    if not view or not view["items"]:
        return
    changes = [
        {"item_id": i["id"], "participant": w.key(m), "on": True}
        for i in view["items"]
        for m in w.rng.sample(NAMES, w.rng.randint(1, 4))
    ]
    await call(
        w,
        d,
        "tick (draft)",
        "PUT",
        f"/api/v1/bills/{bill}/allocations",
        {"changes": changes},
    )
    if not view["reconciliation"].get("ok", True):
        await call(
            w,
            d,
            "override",
            "POST",
            f"/api/v1/bills/{bill}/reconciliation/override",
            {"reason": "sim"},
        )
    await call(w, d, "send", "POST", f"/api/v1/bills/{bill}/send")


PHOTOS = {
    "i01": "i01.jpg",
    "i02": "i02.jpg",
    "i03": "i03.png",
    "i04": "i04.jpg",
    "i07": "i07.jpg",
    "i09": "i09.jpg",
}
MIME = {".jpg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}


async def put_receipt(w: World, d: Device, bill: str, name: str) -> bool:
    data = (FIXTURES / name).read_bytes()
    mime = MIME[Path(name).suffix]
    t = await call(
        w,
        d,
        "upload ticket",
        "POST",
        "/api/v1/uploads/authorize",
        {"bill_id": bill, "mime": mime, "bytes": len(data)},
    )
    if t is None or t.status_code != 200:
        return False
    ticket = t.json()
    if (
        ticket["method"] != "PUT"
    ):  # (every storage adapter hands out a PUT ticket today)
        w.stats.errors.append(f"upload ticket method {ticket['method']}")
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            put = await c.put(
                ticket["upload_url"], content=data, headers=ticket["headers"]
            )
    except httpx.HTTPError:
        return False
    if put.status_code >= 300:
        w.stats.errors.append(f"storage PUT {put.status_code}")
        return False
    r = await call(
        w,
        d,
        "attach receipt",
        "POST",
        f"/api/v1/bills/{bill}/receipts",
        {"storage_key": ticket["storage_key"], "mime": mime, "bytes": len(data)},
    )
    return r is not None and r.status_code in (200, 201)


async def act_photo_bill(w: World, d: Device) -> None:
    if d.member == w.leaver:
        return
    r = await call(
        w, d, "create bill (photo)", "POST", f"/api/v1/houses/{w.house_id}/bills", {}
    )
    if r is None or r.status_code != 201:
        return
    bill = r.json()["id"]
    fid = w.rng.choice(sorted(PHOTOS))
    if not await put_receipt(w, d, bill, PHOTOS[fid]):
        return
    payload = json.loads((FIXTURES / f"{fid}.ocr.json").read_text(encoding="utf-8"))
    o = await call(
        w,
        d,
        "ocr",
        "POST",
        f"/api/v1/bills/{bill}/ocr",
        {"parts": [{"pages": payload["pages"]}]},
    )
    if o is None or o.status_code not in (200, 201):
        return
    result = o.json()
    if result.get("extraction") == "offered":
        ex = result["result"]["extraction_id"]
        await call(
            w, d, "apply scan", "POST", f"/api/v1/bills/{bill}/extractions/{ex}/apply"
        )
    view = await get(w, d, f"/api/v1/bills/{bill}")
    if not view or not view["items"]:
        return
    changes = [
        {"item_id": i["id"], "participant": w.key(m), "on": True}
        for i in view["items"]
        for m in w.rng.sample(NAMES, w.rng.randint(1, 4))
    ]
    await call(
        w,
        d,
        "tick (draft)",
        "PUT",
        f"/api/v1/bills/{bill}/allocations",
        {"changes": changes},
    )
    if not view["reconciliation"].get("ok", True):
        await call(
            w,
            d,
            "override",
            "POST",
            f"/api/v1/bills/{bill}/reconciliation/override",
            {"reason": "sim"},
        )
    await call(w, d, "send", "POST", f"/api/v1/bills/{bill}/send")


async def sleeper(w: World, until: float) -> None:
    """Phones lock and tabs go to the background: a stream disappears for a while, then resumes."""
    while time.monotonic() + 45 < until:
        await asyncio.sleep(w.rng.uniform(5, 20))
        await w.paused.wait()
        d = w.rng.choice(w.devices)
        if d.stream is None or d.stream.asleep:
            continue
        await d.stream.sleep()
        w.stats.actions["tab sleep"] += 1
        await asyncio.sleep(w.rng.uniform(5, 40))
        if d.stream.asleep:  # a checkpoint may already have woken it
            d.stream.start()


async def act_tick(w: World, d: Device) -> None:
    tabs = await open_tabs(w, d)
    if not tabs:
        return
    bill = w.rng.choice(tabs)["id"]
    view = await get(w, d, f"/api/v1/bills/{bill}")
    if not view or not view["items"]:
        return
    item = w.rng.choice(view["items"])
    who = (
        d.member
        if not view["me"]["is_payer"] or w.rng.random() < 0.5
        else w.rng.choice(NAMES)
    )
    await call(
        w,
        d,
        "tick",
        "PUT",
        f"/api/v1/bills/{bill}/allocations",
        {
            "changes": [
                {
                    "item_id": item["id"],
                    "participant": w.key(who),
                    "on": w.rng.random() < 0.6,
                }
            ]
        },
    )


async def act_price_edit(w: World, d: Device) -> None:
    tabs = [
        t
        for t in await open_tabs(w, d)
        if t["payer_member_id"] == w.member_ids[d.member]
    ]
    if not tabs:
        return
    bill = w.rng.choice(tabs)["id"]
    view = await get(w, d, f"/api/v1/bills/{bill}")
    if not view or not view["items"]:
        return
    item = w.rng.choice(view["items"])
    await call(
        w,
        d,
        "price edit",
        "PATCH",
        f"/api/v1/bills/{bill}/items/{item['id']}",
        {"line_total_cents": max(1, item["line_total_cents"] + w.rng.randint(-50, 50))},
        if_match=item["version"],
    )


async def act_pay(w: World, d: Device) -> None:
    tabs = [t for t in await open_tabs(w, d) if t.get("my_owed_cents", 0) > 0]
    if not tabs:
        return
    tab = w.rng.choice(tabs)
    await call(
        w,
        d,
        "pay",
        "POST",
        f"/api/v1/bills/{tab['id']}/payments",
        {
            "participant": w.key(d.member),
            "amount_cents": tab["my_owed_cents"],
            "method": w.rng.choice(["venmo", "zelle", "cash"]),
        },
    )


async def act_confirm(w: World, d: Device) -> None:
    for tab in await open_tabs(w, d):
        if tab["payer_member_id"] != w.member_ids[d.member]:
            continue
        money = await get(w, d, f"/api/v1/bills/{tab['id']}/money")
        for p in (money or {}).get("payments", []):
            if p["status"] == "pending_confirmation":
                verb = "confirm" if w.rng.random() < 0.85 else "reject"
                body = None if verb == "confirm" else {"note": "Not seeing it yet"}
                await call(
                    w, d, verb, "POST", f"/api/v1/payments/{p['id']}/{verb}", body
                )
                return


async def act_settle_or_reopen(w: World, d: Device) -> None:
    tabs = await get(w, d, f"/api/v1/houses/{w.house_id}/tabs")
    if not tabs:
        return
    mine = w.member_ids[d.member]
    if w.rng.random() < 0.7:
        own = [t for t in tabs["open"] if t["payer_member_id"] == mine]
        if own:
            await call(
                w,
                d,
                "settle",
                "POST",
                f"/api/v1/bills/{w.rng.choice(own)['id']}/settle",
                {"confirm_outstanding": True},
            )
    else:
        own = [
            t
            for t in tabs["history"]
            if t["payer_member_id"] == mine and t["state"] == "settled"
        ]
        if own:
            await call(
                w,
                d,
                "reopen",
                "POST",
                f"/api/v1/bills/{w.rng.choice(own)['id']}/reopen",
            )


async def kitchen_items(w: World, d: Device) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for place in ("fridge", "pantry"):
        view = await get(
            w, d, f"/api/v1/houses/{w.house_id}/inventory", params={"place": place}
        )
        if view:
            out += [i for a in view["areas"] for i in a["items"]]
    return out


async def act_kitchen(w: World, d: Device) -> None:
    items = await kitchen_items(w, d)
    if not items:
        return
    item = w.rng.choice(items)
    roll = w.rng.random()
    if item["mine"] and roll < 0.4:
        await call(
            w,
            d,
            "sharing",
            "PUT",
            f"/api/v1/inventory/{item['id']}/sharing",
            {"open": w.rng.random() < 0.5},
        )
    elif item["mine"] and roll < 0.55:
        r = await call(
            w, d, "used up", "POST", f"/api/v1/inventory/{item['id']}/used-up"
        )
        if r is not None and r.status_code == 200 and w.rng.random() < 0.3:
            await call(
                w, d, "restore", "POST", f"/api/v1/inventory/{item['id']}/restore"
            )
    elif item["mine"]:
        await call(
            w, d, "still good", "POST", f"/api/v1/inventory/{item['id']}/still-good"
        )
    elif not item["open_for_me"] and not item.get("my_request"):
        await call(
            w,
            d,
            "ask",
            "POST",
            f"/api/v1/inventory/{item['id']}/requests",
            {"amount": w.rng.choice(["a_bit", "half", "all"]), "note": "for dinner"},
        )


async def act_answer(w: World, d: Device) -> None:
    box = await get(w, d, f"/api/v1/houses/{w.house_id}/requests", params={"box": "in"})
    pending = [r for r in (box or {}).get("requests", []) if r["status"] == "pending"]
    if not pending:
        return
    req = w.rng.choice(pending)
    options = req.get("options", ["free"]) + ["decline"]
    decision = w.rng.choice(options)
    body: dict[str, Any] = {"decision": decision}
    if decision == "chip_in" and req.get("chip_in_suggestion_cents"):
        body["chip_in_cents"] = req["chip_in_suggestion_cents"]
    r = await call(
        w, d, f"answer {decision}", "POST", f"/api/v1/requests/{req['id']}/answer", body
    )
    if r is not None and r.status_code == 200 and w.rng.random() < 0.15:
        await call(w, d, "undo answer", "POST", f"/api/v1/requests/{req['id']}/undo")


async def act_remove_and_rejoin(w: World, admin: Device) -> None:
    """The admin removes Kabir; Kabir comes back through the invite link and gets their old row back."""
    r = await call(
        w,
        admin,
        "remove member",
        "DELETE",
        f"/api/v1/houses/{w.house_id}/members/{w.member_ids['kabir']}",
    )
    if r is None:
        return
    if r.status_code != 200:  # Kabir never pays, so nothing may block this
        w.stats.errors.append(f"remove member: {r.status_code} {r.text[:200]}")
        return
    kabir = [d for d in w.devices if d.member == "kabir"]
    for d in kabir:
        joined = await call(
            w,
            d,
            "rejoin",
            "POST",
            f"/api/v1/join/{w.invite}/claim",
            {"new_name": "Kabir"},
        )
        if joined is not None and joined.status_code in (200, 201):
            w.member_ids["kabir"] = joined.json()["member"]["id"]
            return
    w.stats.errors.append("Kabir could not rejoin")


ACTIONS = (
    (act_tick, 40),
    (act_kitchen, 14),
    (act_answer, 8),
    (act_pay, 8),
    (act_confirm, 6),
    (act_manual_bill, 5),
    (act_pdf_bill, 3),
    (act_photo_bill, 2),
    (act_price_edit, 6),
    (act_settle_or_reopen, 3),
)


async def actor(w: World, d: Device, until: float) -> None:
    funcs, weights = zip(*ACTIONS, strict=True)
    while time.monotonic() < until:
        await w.paused.wait()
        action = w.rng.choices(funcs, weights)[0]
        try:
            await action(w, d)
        except Exception as exc:  # noqa: BLE001 - a crash in the simulation is a finding too
            w.stats.errors.append(
                f"{d.name} {action.__name__}: {type(exc).__name__}: {exc}"
            )
        await asyncio.sleep(w.rng.uniform(0.05, 0.6))


# ---- checks --------------------------------------------------------------------------------------


async def checkpoint(
    w: World, db_url: str, push_url: str | None, final: bool = False
) -> None:
    await w.quiet.acquire()  # no restart or removal half done
    try:
        await _checkpoint(w, db_url, push_url, final)
    finally:
        w.quiet.release()


async def _checkpoint(w: World, db_url: str, push_url: str | None, final: bool) -> None:
    w.paused.clear()  # stop new actions, let the in-flight ones land
    for _ in range(300):
        if w.in_flight == 0:
            break
        await asyncio.sleep(0.05)
    for d in (
        w.devices
    ):  # sleeping tabs come back (from their Last-Event-ID) before the checks
        if d.stream is not None and d.stream.asleep:
            d.stream.start()
    admin = w.devices[0]
    await drain(w)
    await asyncio.sleep(3.0)  # every stream delivers within 2 s (PRD §21.7)
    w.stats.checkpoints += 1
    label = "final" if final else f"checkpoint {w.stats.checkpoints}"
    engine = make_engine(db_url, pool_size=1)
    try:
        async with make_sessionmaker(engine)() as db:
            for v in await check_all(db, datetime.now(UTC)):
                w.stats.violations.append(f"{label}: invariant: {v}")
            from sqlalchemy import text

            latest = int(
                await db.scalar(
                    text("SELECT event_seq FROM houses WHERE id = :h"),
                    {"h": w.house_id},
                )
                or 0
            )
    finally:
        await engine.dispose()
    for d in w.devices:
        if d.stream is None:
            continue
        seqs = d.stream.seqs
        dupes = [s for s, n in Counter(seqs).items() if n > 1]
        if dupes:
            w.stats.violations.append(
                f"{label}: {d.name} got events twice: {dupes[:5]}"
            )
        if seqs and seqs != sorted(seqs):
            w.stats.violations.append(f"{label}: {d.name} got events out of order")
        if d.stream.last is not None and d.stream.last != latest:
            w.stats.violations.append(
                f"{label}: {d.name} stopped at {d.stream.last}, house is at {latest}"
            )
        if seqs:
            missing = sorted(
                set(range(seqs[0], seqs[-1] + 1)) - set(seqs) - d.stream.skipped
            )
            if missing:
                w.stats.violations.append(
                    f"{label}: {d.name} missed events {missing[:5]}"
                )
    for tab in await open_tabs(w, admin):
        views = []
        for d in w.devices:
            v = await get(w, d, f"/api/v1/bills/{tab['id']}")
            if v is not None:
                views.append(
                    json.dumps(
                        [
                            v["shares"],
                            [
                                sorted(a["participant"] for a in i["allocations"])
                                for i in v["items"]
                            ],
                        ],
                        sort_keys=True,
                    )
                )
        if len(set(views)) > 1:
            w.stats.violations.append(f"{label}: devices disagree on tab {tab['id']}")
    if push_url:
        got = (await asyncio.to_thread(httpx.get, f"{push_url}/__test/push")).json()
        per_device: dict[str, Counter[str]] = defaultdict(Counter)
        for g in got:
            if g["answered"] == 201 and isinstance(g["payload"], dict):
                per_device[g["sub_id"]][json.dumps(g["payload"], sort_keys=True)] += 1
        for sub, counts in per_device.items():
            twice = [k[:80] for k, n in counts.items() if n > 1]
            if twice:
                w.stats.violations.append(
                    f"{label}: push device {sub[:6]} got {twice[:3]} twice"
                )
    w.paused.set()


# ---- the server (local target) ---------------------------------------------------------------------


class LocalServer:
    """Several single-process API instances on their own ports, like serverless instances (and not
    `uvicorn --workers`, whose shared socket hangs new connections on Windows; see multiserver.py)."""

    def __init__(
        self, db_url: str, profile: str, ports: list[int], *, host: str = "127.0.0.1"
    ) -> None:
        self.db_url, self.profile, self.ports, self.host = db_url, profile, ports, host
        self.urls = [f"http://127.0.0.1:{port}" for port in ports]
        self.url = self.urls[0]
        self.procs: list[subprocess.Popen[bytes]] = []
        self.logs = [REPO / ".cache" / "simulate" / f"api-{port}.log" for port in ports]

    def env(self, url: str) -> dict[str, str]:
        # The test configuration (fakes on, test routes on), as in tests/conftest.py.
        return {
            **os.environ,
            "APP_ENV": "test",
            "TEST_ROUTES_ENABLED": "true",
            "PROFILE": self.profile,
            "DATABASE_URL": self.db_url,
            "REDIS_URL": "redis://localhost:6379/3",
            "SESSION_SECRET": "sim-session-secret-0123456789abcdef",
            "TOKEN_HASH_PEPPER": "sim-pepper-0123456789abcdef01234567",
            "CRON_SECRET": "sim-cron-secret-0123456789abcdef0123",
            "EMAIL_PROVIDER": "testinbox",
            "EMAIL_POSTAL_ADDRESS": "KitchenTab, 1 Test Street, Testville",
            "TEST_INBOX_SECRET": INBOX_SECRET,
            "SMS_PROVIDER": "fake",
            "EXTRACTOR_PROVIDER": "local",
            "PUSH_PROVIDER": "fake",
            "QUEUE_PROVIDER": "inline",
            "STORAGE_PROVIDER": "s3",
            "S3_ACCESS_KEY": os.environ.get("S3_ACCESS_KEY", "minioadmin"),
            "S3_SECRET_KEY": os.environ.get("S3_SECRET_KEY", "minioadmin"),
            "WEB_ORIGIN": url,
            "PUBLIC_LINK_BASE": url,
            "RATE_LIMIT_WRITES_PER_MIN": "0",
            "RATE_LIMIT_READS_PER_MIN": "0",
            "LOG_LEVEL": "WARNING",
            # 5 + 10 overflow per instance: several instances stay well under Postgres' 100 slots
            "DB_POOL_SIZE": os.environ.get("DB_POOL_SIZE", "5"),
        }

    def start(self) -> None:
        """Start every instance at once and wait for all of them."""
        offsets = []
        self.procs = []
        for port, url, log in zip(self.ports, self.urls, self.logs, strict=True):
            log.parent.mkdir(parents=True, exist_ok=True)
            out = log.open("ab")
            offsets.append(log.stat().st_size)  # the log is appended to across restarts
            self.procs.append(
                spawn_tree(
                    server_cmd(port, self.host),
                    cwd=API_DIR,
                    env=self.env(url),
                    stdout=out,
                )
            )
        for url, proc, log, offset in zip(
            self.urls, self.procs, self.logs, offsets, strict=True
        ):
            wait_workers_ready(url, proc, log, 1, offset=offset)

    def stop(self) -> None:
        """Every instance (a restart, like a redeploy)."""
        for proc in self.procs:
            stop_tree(proc)


async def restarter(w: World, server: LocalServer, every: float, until: float) -> None:
    while time.monotonic() + every < until:
        await asyncio.sleep(every)
        async with w.quiet:
            w.restarting = True
            await asyncio.to_thread(server.stop)
            await asyncio.to_thread(server.start)
            w.stats.restarts += 1
            await asyncio.sleep(2)
            w.restarting = False


def fresh_database(base_url: str, name: str) -> str:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    async def create() -> None:
        engine = make_engine(base_url, pool_size=1)
        async with engine.connect() as conn:
            auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await auto.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            await auto.execute(text(f'CREATE DATABASE "{name}"'))
        await engine.dispose()

    asyncio.run(create())
    url = base_url.rsplit("/", 1)[0] + f"/{name}"
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    return url


# ---- setup and main ----------------------------------------------------------------------------------


async def sign_in(base_url: str, email: str, inbox_secret: str) -> Api:
    client = httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=False)
    api = Api(client)
    sent = await api.post(
        "/api/v1/auth/otp/send", {"channel": "email", "destination": email}, idem=False
    )
    sent.raise_for_status()
    inbox = await client.get(
        "/api/test/inbox",
        params={"to": email, "limit": 1},
        headers={"X-Test-Secret": inbox_secret},
    )
    code = next(
        c for c in inbox.json()[0]["subject"].split() if c.isdigit() and len(c) == 6
    )
    verified = await api.post(
        "/api/v1/auth/otp/verify",
        {"channel": "email", "destination": email, "code": code},
        idem=False,
    )
    verified.raise_for_status()
    api.csrf = None
    api.user_id = verified.json()["user_id"]
    api.email = email
    return api


async def setup(
    base_urls: list[str],
    inbox_secret: str,
    stats: Stats,
    rng: random.Random,
    tag: str,
    push_url: str | None,
) -> World:
    domain = "launch-check.kitchentab.test"
    admin = await sign_in(base_urls[0], f"pranav+sim{tag}@{domain}", inbox_secret)
    r = await admin.post(
        "/api/v1/houses",
        {"name": f"Sim {tag}", "roommates": ["Arjun", "Meera", "Kabir"]},
    )
    r.raise_for_status()
    house = r.json()
    member_ids = {m["display_name"].lower(): m["id"] for m in house["members"]}
    devices = [Device("pranav", "phone", admin)]
    for n, (member, kind) in enumerate(
        DEVICES[1:], start=1
    ):  # devices spread over the instances
        api = await sign_in(
            base_urls[n % len(base_urls)], f"{member}+sim{tag}@{domain}", inbox_secret
        )
        if member != "pranav" and not any(d.member == member for d in devices):
            claim = await api.post(
                f"/api/v1/join/{house['invite_token']}/claim",
                {"member_id": member_ids[member]},
            )
            claim.raise_for_status()
        devices.append(Device(member, kind, api))
    if push_url:
        for d in devices:
            sub = (
                await asyncio.to_thread(
                    httpx.post, f"{push_url}/subscriptions", json={"user": d.name}
                )
            ).json()
            await d.api.post(
                "/api/v1/push/subscriptions",
                {
                    "endpoint": sub["endpoint"],
                    "keys": sub["keys"],
                    "device_label": d.name,
                },
                idem=False,
            )
    w = World(
        base_urls[0],
        house["house"]["id"],
        house["invite_token"],
        member_ids,
        devices,
        stats,
        rng,
    )
    for d in devices:
        # A device's stream usually lives on another instance than its writes.
        n = w.devices.index(d)
        other = base_urls[(n + 1) % len(base_urls)]
        d.stream = Stream(
            Api(
                httpx.AsyncClient(
                    base_url=other, cookies=d.api.http.cookies, timeout=30
                )
            ),
            w.house_id,
            stats,
        )
        d.stream.start()
    w.paused.set()
    return w


async def drainer(w: World, until: float) -> None:
    while time.monotonic() < until:
        await asyncio.sleep(2)
        if w.paused.is_set():
            await drain(w)


async def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    stats = Stats()
    server: LocalServer | None = None
    push_ctx = None
    push_url: str | None = None
    if args.target == "local":
        db_url = await asyncio.to_thread(
            fresh_database,
            asyncpg_url(args.database_url),
            f"kitchentab_sim_{args.seed}",
        )
        push_port = free_port()
        from fakeservices.fake_push import create_app as create_fake_push

        push_ctx = serve(create_fake_push(f"http://127.0.0.1:{push_port}"), push_port)
        push_url = push_ctx.__enter__()
        server = LocalServer(
            db_url, args.profile, [free_port() for _ in range(args.instances)]
        )
        await asyncio.to_thread(server.start)
        base_urls = server.urls
        inbox_secret = INBOX_SECRET
    else:
        base_urls, db_url, inbox_secret = (
            [args.base_url],
            asyncpg_url(args.database_url),
            args.inbox_secret,
        )
    started = time.monotonic()
    until = started + args.minutes * 60
    try:
        w = await setup(
            base_urls,
            inbox_secret,
            stats,
            rng,
            f"{args.seed}x{uuid.uuid4().hex[:4]}",
            push_url,
        )
        tasks = [asyncio.create_task(actor(w, d, until)) for d in w.devices]
        tasks.append(asyncio.create_task(drainer(w, until)))
        tasks.append(asyncio.create_task(sleeper(w, until)))
        if server is not None and args.restart_every:
            tasks.append(
                asyncio.create_task(restarter(w, server, args.restart_every, until))
            )
        if args.remove_rejoin:
            w.leaver = "kabir"

            async def churn() -> (
                None
            ):  # Kabir is removed and comes back, a few times a run
                every = max(45.0, args.minutes * 60 / 4)
                while time.monotonic() + every < until:
                    await asyncio.sleep(every)
                    async with w.quiet:
                        await act_remove_and_rejoin(w, w.devices[0])

            tasks.append(asyncio.create_task(churn()))
        while time.monotonic() < until:
            await asyncio.sleep(min(30, max(0.0, until - time.monotonic())))
            if time.monotonic() < until:
                await checkpoint(w, db_url, push_url)
        await asyncio.gather(*tasks, return_exceptions=True)
        await checkpoint(w, db_url, push_url, final=True)
        for d in w.devices:
            if d.stream:
                await d.stream.stop()
                await d.stream.api.http.aclose()
            await d.api.http.aclose()
    finally:
        if server is not None:
            server.stop()
        if push_ctx is not None:
            push_ctx.__exit__(None, None, None)
    summary = {
        "target": args.target,
        "profile": args.profile,
        "seed": args.seed,
        "minutes": args.minutes,
        "actions": sum(stats.actions.values()),
        "by_action": dict(stats.actions.most_common()),
        "statuses": dict(stats.statuses.most_common()),
        "checkpoints": stats.checkpoints,
        "restarts": stats.restarts,
        "stream_reconnects": stats.reconnects,
        "stream_endings": dict(stats.stream_endings.most_common()),
        "errors": stats.errors[:50],
        "violations": stats.violations[:50],
        "result": "PASS" if not stats.errors and not stats.violations else "FAIL",
    }
    out = REPO / ".cache" / "simulate" / f"summary-{args.target}-{args.seed}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["result"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--target", choices=["local", "local-vercel"], default="local")
    parser.add_argument("--profile", choices=["local", "vercel"], default="local")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument(
        "--instances", type=int, default=3, help="API instances (local)"
    )
    parser.add_argument(
        "--restart-every",
        type=float,
        default=300,
        help="seconds between server restarts (local)",
    )
    parser.add_argument(
        "--remove-rejoin", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--base-url", default=os.environ.get("SIM_BASE_URL", ""))
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "SIM_DATABASE_URL",
            "postgresql+asyncpg://kitchentab:kitchentab@localhost:5432/kitchentab",
        ),
    )
    parser.add_argument(
        "--inbox-secret", default=os.environ.get("SIM_INBOX_SECRET", "")
    )
    args = parser.parse_args()
    if args.target == "local-vercel" and not (args.base_url and args.inbox_secret):
        parser.error("--target local-vercel needs --base-url and --inbox-secret")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
