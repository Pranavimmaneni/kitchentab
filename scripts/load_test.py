"""Load tests (`make test-load`; TEST_PLAN PERF-01, PERF-02).

PERF-01  k6 (load/perf01.js): 200 concurrent people editing 50 open bills, 4 per bill (ticks, If-Match
         price edits by the payer, bill and tab reads). Pass: p95 API < 300 ms, 0 errors.
PERF-02  1,000 open event streams across 200 houses while every house keeps writing. Pass: event
         delivery p95 < 2 s (send of the write to arrival on each stream), nothing missed.

The driver starts its own API (4 workers, test configuration, real rate limits off: one address
sends every request) on a fresh database, mints sessions for 350 people straight in the database
(signing in 350 people through email codes is not what is being measured), builds the houses and bills
through the API, runs k6 from its Docker image against it, then the stream fan-out from this process.

    uv run --project apps/api python scripts/load_test.py [--only perf01|perf02] [--minutes 2]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import statistics
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "api"))
sys.path.insert(0, str(REPO / "scripts"))

from app.db import make_engine, make_sessionmaker
from app.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.services.auth import resolve_user, start_session
from app.services.invariants import check_all
from app.settings import asyncpg_url
from simulate_household import LocalServer, fresh_database
from tests.harness.servers import free_port

OUT = REPO / ".cache" / "load"
K6_IMAGE = "grafana/k6:0.54.0"
P95_API_MS = 300
P95_DELIVERY_S = 2.0


class Person:
    def __init__(self, base_url: str, token: str) -> None:
        self.token = token
        self.csrf = (
            uuid.uuid4().hex
        )  # double-submit: any value works when cookie and header match
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=60,
            cookies={SESSION_COOKIE: token, CSRF_COOKIE: self.csrf},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        )

    async def send(
        self, method: str, url: str, body: Any = None, **headers: str
    ) -> httpx.Response:
        h = {CSRF_HEADER: self.csrf, **headers}
        if method == "POST" and "Idempotency-Key" not in h:
            h["Idempotency-Key"] = str(uuid.uuid4())
        return await self.http.request(method, url, json=body, headers=h)

    async def ok(self, method: str, url: str, body: Any = None) -> Any:
        r = await self.send(method, url, body)
        if r.status_code >= 300:
            raise RuntimeError(f"{method} {url}: {r.status_code} {r.text[:300]}")
        return r.json()


async def mint_people(db_url: str, n: int, tag: str) -> list[str]:
    engine = make_engine(db_url, pool_size=2)
    tokens = []
    try:
        async with make_sessionmaker(engine)() as db:
            now = datetime.now(UTC)
            for i in range(n):
                email = f"load{i}+{tag}@launch-check.kitchentab.test"
                user = await resolve_user(
                    db,
                    "email",
                    email,
                    email=email,
                    phone=None,
                    display_name=f"Load {i}",
                    now=now,
                )
                tokens.append(
                    await start_session(
                        db, user, now, user_agent="load-test", ip_hash=None
                    )
                )
            await db.commit()
    finally:
        await engine.dispose()
    return tokens


async def build_house(
    admin: Person, roommates: list[Person], idx: int, items: int
) -> dict[str, Any]:
    """A house, its people (roommates claim their names by link) and one open bill split four ways."""
    house = await admin.ok(
        "POST",
        "/api/v1/houses",
        {"name": f"Load {idx}", "roommates": ["Arjun", "Meera", "Kabir"]},
    )
    hid = house["house"]["id"]
    member_ids = [m["id"] for m in house["members"]]
    for person, member in zip(roommates, member_ids[1:], strict=False):
        await person.ok(
            "POST", f"/api/v1/join/{house['invite_token']}/claim", {"member_id": member}
        )
    bill = (
        await admin.ok(
            "POST",
            f"/api/v1/houses/{hid}/bills",
            {"store_name": "Load Mart", "purchase_date": "2026-10-01"},
        )
    )["id"]
    item_ids = []
    for n in range(items):
        made = await admin.ok(
            "POST",
            f"/api/v1/bills/{bill}/items",
            {
                "name": f"Item {n}",
                "line_total_cents": 199 + 37 * n,
                "storage": "none",
                "category": "other",
            },
        )
        item_ids.append(made["id"])
    changes = [
        {"item_id": i, "participant": f"m:{m}", "on": True}
        for i in item_ids
        for m in member_ids
    ]
    await admin.ok("PUT", f"/api/v1/bills/{bill}/allocations", {"changes": changes})
    await admin.ok("POST", f"/api/v1/bills/{bill}/send")
    return {"house": hid, "bill": bill, "items": item_ids, "members": member_ids}


async def prepare(
    base_urls: list[str], db_url: str, houses: int, signed_in_houses: int, items: int
) -> dict[str, Any]:
    n_people = houses + 3 * signed_in_houses
    tokens = await mint_people(db_url, n_people, uuid.uuid4().hex[:6])
    people = [Person(base_urls[n % len(base_urls)], t) for n, t in enumerate(tokens)]
    admins, rest = people[:houses], people[houses:]
    sem = asyncio.Semaphore(16)

    async def one(i: int) -> dict[str, Any]:
        async with sem:
            mates = rest[3 * i : 3 * i + 3] if i < signed_in_houses else []
            built = await build_house(admins[i], mates, i, items)
            built["people"] = [p.token for p in [admins[i], *mates]]
            built["csrf"] = [p.csrf for p in [admins[i], *mates]]
            return built

    t0 = time.monotonic()
    built = await asyncio.gather(*(one(i) for i in range(houses)))
    for p in people:
        await p.http.aclose()
    print(
        f"prepared {houses} houses, {n_people} people in {time.monotonic() - t0:.0f} s",
        flush=True,
    )
    return {"houses": built}


# ---- PERF-01: k6 ---------------------------------------------------------------------------------


def run_k6(ports: list[int], world: dict[str, Any], minutes: float) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    editors = [h for h in world["houses"] if len(h["people"]) == 4]
    (OUT / "perf01-world.json").write_text(
        json.dumps({"houses": editors}), encoding="utf-8"
    )
    summary = OUT / "perf01-summary.json"
    summary.unlink(missing_ok=True)
    cmd = [
        "docker", "run", "--rm",
        "--add-host", "host.docker.internal:host-gateway",
        "-v", f"{REPO / 'load'}:/scripts:ro",
        "-v", f"{OUT}:/out",
        "-e", "BASE_URLS=" + ",".join(f"http://host.docker.internal:{p}" for p in ports),
        "-e", f"DURATION={int(minutes * 60)}s",
        "-e", f"CSRF_COOKIE={CSRF_COOKIE}",
        "-e", f"SESSION_COOKIE={SESSION_COOKIE}",
        "-e", f"CSRF_HEADER={CSRF_HEADER}",
        K6_IMAGE, "run", "--quiet", "--summary-export", "/out/perf01-summary.json", "/scripts/perf01.js",
    ]  # fmt: skip
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    (OUT / "perf01-k6.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    if not summary.exists():
        raise RuntimeError(
            f"k6 produced no summary (exit {proc.returncode}); see {OUT / 'perf01-k6.log'}"
        )
    data = json.loads(summary.read_text(encoding="utf-8"))
    m = data["metrics"]
    dur = (
        m["http_req_duration{api:yes}"]
        if "http_req_duration{api:yes}" in m
        else m["http_req_duration"]
    )
    failed = m["http_req_failed"]
    result = {
        "id": "PERF-01",
        "people": len(editors) * 4,
        "bills": len(editors),
        "seconds": round(time.monotonic() - t0),
        "requests": int(m["http_reqs"]["count"]),
        "rps": round(m["http_reqs"]["rate"], 1),
        "p95_ms": round(dur["p(95)"], 1),
        "p99_ms": round(dur.get("p(99)", 0.0), 1),
        "median_ms": round(dur["med"], 1),
        "error_rate": failed.get("value", failed.get("rate", 0.0)),
        "k6_exit": proc.returncode,
    }
    result["pass"] = (
        result["p95_ms"] < P95_API_MS
        and result["error_rate"] == 0
        and proc.returncode == 0
    )
    return result


# ---- PERF-02: stream fan-out ---------------------------------------------------------------------


async def run_fanout(
    base_urls: list[str], world: dict[str, Any], per_house: int, minutes: float
) -> dict[str, Any]:
    houses = world["houses"]
    sent: dict[str, float] = {}  # client mutation id -> when the write was sent
    owner: dict[str, int] = {}  # client mutation id -> house index
    arrivals: list[float] = []
    seen: dict[tuple[int, int], set[str]] = {}
    ready = 0
    errors: list[str] = []
    stop = asyncio.Event()
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)

    async def stream(h: dict[str, Any], k: int) -> None:
        nonlocal ready
        token, csrf = h["people"][0], h["csrf"][0]
        got = seen.setdefault((houses.index(h), k), set())
        last: str | None = None
        first = True
        async with httpx.AsyncClient(  # streams spread over every instance
            base_url=base_urls[(houses.index(h) * per_house + k) % len(base_urls)],
            timeout=None,
            limits=limits,
            cookies={SESSION_COOKIE: token, CSRF_COOKIE: csrf},
        ) as c:
            while not stop.is_set():
                headers = {
                    "Accept": "text/event-stream",
                    **({"Last-Event-ID": last} if last else {}),
                }
                try:
                    async with c.stream(
                        "GET", f"/api/v1/houses/{h['house']}/events", headers=headers
                    ) as r:
                        if r.status_code != 200:
                            errors.append(f"stream {r.status_code}")
                            await asyncio.sleep(1)
                            continue
                        if first:
                            ready += 1
                            first = False
                        data = event = None
                        async for line in r.aiter_lines():
                            if line.startswith("id:"):
                                last = line[3:].strip()
                            elif line.startswith("event:"):
                                event = line[6:].strip()
                            elif line.startswith("data:"):
                                data = line[5:].strip()
                            elif line == "":
                                if data and event is None:
                                    cmid = json.loads(data).get("client_mutation_id")
                                    if cmid in sent:
                                        arrivals.append(time.monotonic() - sent[cmid])
                                        got.add(cmid)
                                data = event = None
                except (httpx.HTTPError, OSError) as exc:
                    if not stop.is_set():
                        errors.append(f"stream dropped: {type(exc).__name__}")
                        await asyncio.sleep(0.5)

    async def writer(h: dict[str, Any], rng: random.Random, until: float) -> None:
        person = Person(base_urls[houses.index(h) % len(base_urls)], h["people"][0])
        person.csrf = h["csrf"][0]
        person.http.cookies.set(CSRF_COOKIE, person.csrf)
        try:
            while time.monotonic() < until:
                cmid = str(uuid.uuid4())
                item = rng.choice(h["items"])
                body = {
                    "changes": [
                        {
                            "item_id": item,
                            "participant": f"m:{h['members'][0]}",
                            "on": rng.random() < 0.5,
                        }
                    ]
                }
                sent[cmid] = time.monotonic()
                owner[cmid] = houses.index(h)
                try:
                    r = await person.send(
                        "PUT",
                        f"/api/v1/bills/{h['bill']}/allocations",
                        body,
                        **{"X-Client-Mutation-Id": cmid},
                    )
                    if r.status_code != 200 or not r.json().get("applied"):
                        sent.pop(cmid, None)  # nothing changed, so no event to wait for
                        if r.status_code >= 500:
                            errors.append(f"write {r.status_code}")
                except httpx.HTTPError as exc:
                    sent.pop(cmid, None)
                    errors.append(f"write failed: {type(exc).__name__}")
                await asyncio.sleep(rng.uniform(1.0, 3.0))
        finally:
            await person.http.aclose()

    streams = [
        asyncio.create_task(stream(h, k)) for h in houses for k in range(per_house)
    ]
    t0 = time.monotonic()
    while ready < len(streams) and time.monotonic() - t0 < 180:
        await asyncio.sleep(0.5)
    connect_s = time.monotonic() - t0
    print(f"{ready}/{len(streams)} streams open in {connect_s:.0f} s", flush=True)
    rng = random.Random(2)
    until = time.monotonic() + minutes * 60
    await asyncio.gather(
        *(writer(h, random.Random(rng.random()), until) for h in houses)
    )
    await asyncio.sleep(5)  # the last events arrive
    stop.set()
    for t in streams:
        t.cancel()
    await asyncio.gather(*streams, return_exceptions=True)
    # Every stream of a house must have seen every applied write of that house.
    writes_of: dict[int, set[str]] = {}
    for cmid in sent:
        writes_of.setdefault(owner[cmid], set()).add(cmid)
    missed = sum(len(writes_of.get(hi, set()) - got) for (hi, _k), got in seen.items())
    arrivals.sort()
    p95 = arrivals[int(len(arrivals) * 0.95) - 1] if arrivals else float("inf")
    result = {
        "id": "PERF-02",
        "streams": len(streams),
        "streams_open": ready,
        "houses": len(houses),
        "connect_s": round(connect_s, 1),
        "writes_seen": len(sent),
        "deliveries": len(arrivals),
        "p50_s": round(statistics.median(arrivals), 3) if arrivals else None,
        "p95_s": round(p95, 3),
        "max_s": round(arrivals[-1], 3) if arrivals else None,
        "missed_deliveries": missed,
        "errors": errors[:20],
        "error_count": len(errors),
    }
    result["pass"] = (
        ready == len(streams) and p95 < P95_DELIVERY_S and missed == 0 and not errors
    )
    return result


async def main_async(args: argparse.Namespace) -> int:
    db_url = await asyncio.to_thread(
        fresh_database, asyncpg_url(args.database_url), "kitchentab_load"
    )
    ports = [free_port() for _ in range(args.instances)]
    server = LocalServer(db_url, args.profile, ports, host="0.0.0.0")
    server.logs = [OUT / f"api-{port}.log" for port in ports]
    OUT.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(server.start)
    results: list[dict[str, Any]] = []
    try:
        world = await prepare(
            server.urls, db_url, houses=200, signed_in_houses=50, items=12
        )
        if args.only in (None, "perf01"):
            results.append(await asyncio.to_thread(run_k6, ports, world, args.minutes))
            print(json.dumps(results[-1]), flush=True)
        if args.only in (None, "perf02"):
            results.append(
                await run_fanout(server.urls, world, per_house=5, minutes=args.minutes)
            )
            print(json.dumps(results[-1]), flush=True)
        engine = make_engine(db_url, pool_size=1)
        async with make_sessionmaker(engine)() as db:
            violations = await check_all(db, datetime.now(UTC))
        await engine.dispose()
        results.append(
            {"id": "invariants", "violations": violations[:20], "pass": not violations}
        )
    finally:
        with contextlib.suppress(Exception):
            server.stop()
    report = {
        "profile": args.profile,
        "instances": args.instances,
        "results": results,
        "pass": all(r["pass"] for r in results),
    }
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", choices=["perf01", "perf02"])
    parser.add_argument("--minutes", type=float, default=2)
    parser.add_argument(
        "--instances", type=int, default=4, help="API instances (ADR-0029)"
    )
    parser.add_argument("--profile", choices=["local", "vercel"], default="local")
    parser.add_argument(
        "--database-url",
        default="postgresql+asyncpg://kitchentab:kitchentab@localhost:5432/kitchentab",
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
