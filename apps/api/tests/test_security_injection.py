"""SEC-06: SQL-injection and XSS probes on every free-text field (TEST_PLAN 3.10).

Every probe is stored exactly as typed (whitespace aside: names collapse runs of spaces) and comes
back as a JSON string with a JSON content type, so nothing is ever interpreted by SQL or by a
browser. Nothing answers 5xx. Search treats `%`, `_`, `'`, `--`, `\\` and NUL as text and never
reaches outside the house. The two HTML confirmation pages escape their parameters, and the CSV
export can't smuggle a spreadsheet formula.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.notify import TYPE_LABELS, unsubscribe_sig
from app.settings import Settings
from tests.harness.api import Api
from tests.harness.security_world import ADMIN, MEMBER, PAYER, HouseWorld, build_house, ok
from tests.harness.tags import fr, tid

ApiFactory = Callable[[], Api]

PROBES = [
    "' OR '1'='1",
    "'; DROP TABLE bills;--",
    "1); DELETE FROM houses WHERE (1=1",
    "\\'; SELECT pg_sleep(3);--",
    '" OR ""="',
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "<svg/onload=alert(1)>",
    "<img src=x onerror=1>",
    "javascript:alert(document.cookie)",
    "{{7*7}}${7*7}#{7*7}",
    "%_\\ ' -- /* */",
    "Zoë's 🍕 & <b>bold</b>",
]
# Four that fit every field (24 chars), for the fields that share storage and rendering with one
# that gets the full set.
CORE = ["' OR '1'='1", "'; DROP TABLE bills;--", "<svg/onload=alert(1)>", "<img src=x onerror=1>"]
# One string with both kinds, for fields that only take one value per tab or item.
COMBINED = "<script>alert(1)</script>'; DROP TABLE bills;--"


def fitting(max_len: int) -> list[str]:
    return [p for p in PROBES if len(p) <= max_len]


def strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


@dataclass
class Field:
    name: str
    max_len: int
    write: Callable[[str], Awaitable[httpx.Response]]
    read: Callable[[], Awaitable[httpx.Response]]
    probes: list[str] | None = None


def is_json(resp: httpx.Response) -> bool:
    return resp.headers.get("content-type", "").split(";")[0] in ("application/json", "application/problem+json")


async def guarded(problems: list[str], label: str, send: Awaitable[httpx.Response]) -> httpx.Response | None:
    """The in-process transport re-raises server errors: record them as the 5xx they would be."""
    try:
        resp = await send
    except Exception as exc:
        problems.append(f"{label}: server error {exc!r}")
        return None
    if resp.status_code >= 500:
        problems.append(f"{label}: {resp.status_code}")
        return None
    return resp


def fields(h: HouseWorld, api: ApiFactory) -> list[Field]:
    admin, payer, member = h.who[ADMIN], h.who[PAYER], h.who[MEMBER]
    created: dict[str, str] = {}

    async def new_house(p: str) -> httpx.Response:
        resp = await admin.post("/api/v1/houses", {"name": p, "roommates": []})
        if resp.status_code == 201:
            created["house"] = resp.json()["house"]["id"]
        return resp

    async def roommate(p: str) -> httpx.Response:
        resp = await admin.post("/api/v1/houses", {"name": "Probe flat", "roommates": [p]})
        if resp.status_code == 201:
            created["roommates"] = resp.json()["house"]["id"]
        return resp

    async def rename_house(p: str) -> httpx.Response:
        return await admin.call("PATCH", f"/api/v1/houses/{h.house_id}", {"name": p}, if_match=await h.house_version())

    async def add_member(p: str) -> httpx.Response:
        return await admin.post(f"/api/v1/houses/{h.house_id}/members", {"name": p})

    async def rename_member(p: str) -> httpx.Response:
        version = await h.member_version(h.placeholder_member)
        url = f"/api/v1/houses/{h.house_id}/members/{h.placeholder_member}"
        return await admin.call("PATCH", url, {"name": p}, if_match=version)

    async def joiner(p: str) -> httpx.Response:
        who = api()
        await who.sign_in(f"joiner-{uuid.uuid4().hex[:8]}@example.com")
        return await who.post(f"/api/v1/join/{h.invite_token}/claim", {"new_name": p})

    async def linker(p: str) -> httpx.Response:
        who = api()
        await who.sign_in(f"linker-{uuid.uuid4().hex[:8]}@example.com")
        return await who.post(f"/api/v1/b/{h.link_token}/claim", {"new_name": p})

    async def store(p: str) -> httpx.Response:
        resp = await payer.post(f"/api/v1/houses/{h.house_id}/bills", {"store_name": p})
        if resp.status_code == 201:
            created["bill"] = resp.json()["id"]
        return resp

    async def edit_store(p: str) -> httpx.Response:
        version = await h.bill_version(h.draft_id)
        return await payer.call("PATCH", f"/api/v1/bills/{h.draft_id}", {"store_name": p}, if_match=version)

    async def item(p: str) -> httpx.Response:
        return await payer.post(f"/api/v1/bills/{h.draft_id}/items", {"name": p, "line_total_cents": 10})

    async def edit_item(p: str) -> httpx.Response:
        version = await h.item_version(h.draft_id, h.draft_items[1])
        url = f"/api/v1/bills/{h.draft_id}/items/{h.draft_items[1]}"
        return await payer.call("PATCH", url, {"name": p}, if_match=version)

    async def fee(p: str) -> httpx.Response:
        version = await h.bill_version(h.draft_id)
        body = {"fees": [{"kind": "fee", "label": p, "amount_cents": 25}]}
        return await payer.call("PATCH", f"/api/v1/bills/{h.draft_id}", body, if_match=version)

    async def override(p: str) -> httpx.Response:
        version = await h.bill_version(h.draft_id)
        await payer.call("PATCH", f"/api/v1/bills/{h.draft_id}", {"receipt_total_cents": 99_999}, if_match=version)
        return await payer.post(f"/api/v1/bills/{h.draft_id}/reconcile-override", {"reason": p})

    async def guest(p: str) -> httpx.Response:
        return await payer.post(f"/api/v1/bills/{h.draft_id}/guests", {"name": p})

    async def pay_note(p: str) -> httpx.Response:
        return await member.post(f"/api/v1/bills/{h.open_id}/payments", {"amount_cents": 1, "note": p})

    async def reject_note(p: str) -> httpx.Response:
        paid = ok(await member.post(f"/api/v1/bills/{h.open_id}/payments", {"amount_cents": 2}), 201)
        return await payer.post(f"/api/v1/payments/{paid['id']}/reject", {"note": p})

    async def ask_note(p: str) -> httpx.Response:
        return await payer.post(f"/api/v1/inventory/{h.inv_item_id}/requests", {"amount": "half", "note": p})

    async def leftovers(p: str) -> httpx.Response:
        return await member.post(f"/api/v1/houses/{h.house_id}/inventory", {"name": p})

    async def edit_leftovers(p: str) -> httpx.Response:
        version = await h.inventory_version(h.inv_item_id)
        return await member.call("PATCH", f"/api/v1/inventory/{h.inv_item_id}", {"name": p}, if_match=version)

    async def display_name(p: str) -> httpx.Response:
        return await member.call("PATCH", "/api/v1/me", {"display_name": p})

    async def zelle(p: str) -> httpx.Response:
        return await member.call("PUT", "/api/v1/me/payment-handles", {"zelle": p})

    async def device(p: str) -> httpx.Response:
        body = {
            "endpoint": f"https://push.test/send/{uuid.uuid4().hex}",
            "keys": {"p256dh": "B" * 87, "auth": "A" * 22},
            "device_label": p,
        }
        return await member.post("/api/v1/push/subscriptions", body, idem=False)

    def reader(who: Api, url: str | Callable[[], str]) -> Callable[[], Awaitable[httpx.Response]]:
        async def read() -> httpx.Response:
            return await who.get(url() if callable(url) else url)

        return read

    house = f"/api/v1/houses/{h.house_id}"
    draft = f"/api/v1/bills/{h.draft_id}"
    return [
        Field("house name (create)", 40, new_house, reader(admin, lambda: f"/api/v1/houses/{created['house']}")),
        Field("house name (rename)", 40, rename_house, reader(admin, house), CORE),
        Field(
            "roommate name (create)",
            24,
            roommate,
            reader(admin, lambda: f"/api/v1/houses/{created['roommates']}"),
            CORE,
        ),
        Field("member name (add)", 24, add_member, reader(admin, house)),
        Field("member name (rename)", 24, rename_member, reader(admin, house), [f"R{p}" for p in CORE]),
        Field("new name (invite claim)", 24, joiner, reader(admin, house), ["J' OR '1'='1", "J<svg/onload=alert(1)>"]),
        Field("new name (bill link claim)", 24, linker, reader(admin, house), ["L'; DROP TABLE bills;--"]),
        Field("store name (create)", 80, store, reader(payer, lambda: f"/api/v1/bills/{created['bill']}")),
        Field("store name (edit)", 80, edit_store, reader(payer, draft), CORE),
        Field("item name (add)", 120, item, reader(payer, draft)),
        Field("item name (edit)", 120, edit_item, reader(payer, draft), CORE),
        Field("fee label", 40, fee, reader(payer, draft), CORE),
        Field("reconcile override reason", 200, override, reader(payer, draft), CORE),
        Field("guest name", 24, guest, reader(payer, draft), CORE),
        Field("payment note", 280, pay_note, reader(payer, f"/api/v1/bills/{h.open_id}/money"), CORE),
        Field("reject note", 280, reject_note, reader(member, f"/api/v1/bills/{h.open_id}/money"), [COMBINED]),
        Field("ask note", 140, ask_note, reader(member, f"{house}/requests?box=in"), [COMBINED]),
        Field("leftovers name (add)", 80, leftovers, reader(member, f"{house}/inventory?place=fridge")),
        Field("leftovers name (edit)", 80, edit_leftovers, reader(member, f"{house}/inventory?place=fridge"), CORE),
        Field("display name", 60, display_name, reader(member, "/api/v1/me")),
        Field("zelle handle", 120, zelle, reader(member, "/api/v1/me/payment-handles"), CORE),
        Field("push device label", 60, device, reader(member, "/api/v1/push/subscriptions"), CORE),
    ]


@tid("SEC-06")
@fr("FR-02", "FR-03", "FR-14", "FR-25", "FR-36", "FR-71", "FR-79")
async def test_SEC_06_text_fields_store_probes_verbatim_as_json(api: ApiFactory) -> None:
    h = await build_house(api, receipts=False)
    problems: list[str] = []
    for f in fields(h, api):
        for probe in f.probes or fitting(f.max_len):
            label = f"{f.name} <- {probe!r}"
            wrote = await guarded(problems, label, f.write(probe))
            if wrote is None:
                continue
            if wrote.status_code not in (200, 201):
                problems.append(f"{label}: write answered {wrote.status_code} {wrote.text[:160]}")
                continue
            if wrote.content and not is_json(wrote):
                problems.append(f"{label}: write answered {wrote.headers.get('content-type')}")
            read = await guarded(problems, label, f.read())
            if read is None:
                continue
            if read.status_code != 200 or not is_json(read):
                problems.append(f"{label}: read answered {read.status_code} {read.headers.get('content-type')}")
            elif " ".join(probe.split()) not in set(strings(read.json())):
                problems.append(f"{label}: not returned verbatim")
    # The probes never ran: every table is still there and still answers.
    for url in (f"/api/v1/houses/{h.house_id}", f"/api/v1/bills/{h.open_id}", f"/api/v1/houses/{h.house_id}/tabs"):
        assert (await h.who[ADMIN].get(url)).status_code == 200, url
    assert not problems, "\n".join(problems)


@tid("SEC-06")
async def test_SEC_06_rejected_input_comes_back_as_json_not_html(api: ApiFactory) -> None:
    """Validation errors may echo the input; they must do it in problem+json, never in HTML."""
    h = await build_house(api, receipts=False)
    payer = h.who[PAYER]
    too_long = "<script>alert(1)</script>" * 10
    for resp in (
        await payer.post("/api/v1/houses", {"name": too_long}),
        await payer.post(f"/api/v1/bills/{h.draft_id}/items", {"name": too_long * 2, "line_total_cents": 1}),
        await payer.call("PUT", "/api/v1/me/notification-prefs", {"push": {too_long: True}}),
        await payer.call("PATCH", "/api/v1/me", {"timezone": too_long}),
        await payer.get(f"/api/v1/houses/{h.house_id}/search", params={"q": too_long}),
        await payer.get(f"/api/v1/bills/{too_long}"),
    ):
        assert 400 <= resp.status_code < 500, (resp.request.url, resp.status_code)
        assert is_json(resp), (resp.request.url, resp.headers.get("content-type"))
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


SEARCH_PROBES = ["%", "%%", "_", "__", "'", "''", "--", "' OR 1=1--", "%' OR '1'='1", "\\", "a\\", "\\%", "%\\"]


@tid("SEC-06")
@fr("FR-53")
async def test_SEC_06_search_treats_wildcards_quotes_and_comments_as_text(api: ApiFactory) -> None:
    h = await build_house(api, receipts=False)
    other = await build_house(api, receipts=False)
    mine = {h.open_id}  # search lists sent tabs only
    problems: list[str] = []
    for q in SEARCH_PROBES + [f"x{q}x" for q in SEARCH_PROBES]:
        resp = await guarded(
            problems, f"search {q!r}", h.who[MEMBER].get(f"/api/v1/houses/{h.house_id}/search", params={"q": q})
        )
        if resp is None:
            continue
        if resp.status_code not in (200, 422) or not is_json(resp):
            problems.append(f"search {q!r}: {resp.status_code} {resp.text[:120]}")
            continue
        if resp.status_code == 200:
            found = {b["id"] for b in resp.json()}
            if not found <= mine:
                problems.append(f"search {q!r} returned tabs from outside the house: {found - mine}")
            if other.open_id in found:
                problems.append(f"search {q!r} reached house 2")
    hit = await h.who[MEMBER].get(f"/api/v1/houses/{h.house_id}/search", params={"q": "milk"})
    assert hit.status_code == 200 and [b["id"] for b in hit.json()] == [h.open_id], hit.text
    assert not problems, "\n".join(problems)


@tid("SEC-06")
async def test_SEC_06_nul_and_control_characters_are_refused_not_500(api: ApiFactory) -> None:
    """Postgres text can't hold NUL: a request carrying one must be a 4xx (or stored without it)."""
    h = await build_house(api, receipts=False)
    payer, member = h.who[PAYER], h.who[MEMBER]
    nul = "milk\x00bread"
    attempts: list[tuple[str, Callable[[], Awaitable[httpx.Response]]]] = [
        ("house name", lambda: payer.post("/api/v1/houses", {"name": nul})),
        ("store name", lambda: payer.post(f"/api/v1/houses/{h.house_id}/bills", {"store_name": nul})),
        ("item name", lambda: payer.post(f"/api/v1/bills/{h.draft_id}/items", {"name": nul, "line_total_cents": 1})),
        ("payment note", lambda: member.post(f"/api/v1/bills/{h.open_id}/payments", {"amount_cents": 1, "note": nul})),
        ("leftovers name", lambda: member.post(f"/api/v1/houses/{h.house_id}/inventory", {"name": nul})),
        ("display name", lambda: member.call("PATCH", "/api/v1/me", {"display_name": nul})),
        ("zelle handle", lambda: member.call("PUT", "/api/v1/me/payment-handles", {"zelle": nul})),
        ("search", lambda: member.get(f"/api/v1/houses/{h.house_id}/search", params={"q": nul})),
    ]
    problems: list[str] = []
    for label, send in attempts:
        resp = await guarded(problems, f"{label} with NUL", send())
        if resp is None:
            continue
        if resp.status_code in (200, 201) and "\x00" in "".join(strings(resp.json())):
            problems.append(f"{label} with NUL: echoed the NUL back")
        elif not (resp.status_code in (200, 201) or 400 <= resp.status_code < 500):
            problems.append(f"{label} with NUL: {resp.status_code}")
    assert not problems, "\n".join(problems)


@tid("SEC-06")
async def test_SEC_06_unsubscribe_pages_escape_their_parameters(api: ApiFactory, settings: Settings) -> None:
    """GET /api/v1/email/unsubscribe is HTML: reflected `t` and `s` are escaped and CSP denies scripts."""
    who = api()
    user_id = (await who.sign_in(f"unsub-{uuid.uuid4().hex[:8]}@example.com"))["user_id"]
    script, attr = "<script>alert(1)</script>", '"><img src=x onerror=alert(1)>'
    page = await who.get("/api/v1/email/unsubscribe", params={"u": user_id, "t": script, "s": attr})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "<script>alert" not in page.text and "<img src=x" not in page.text and 'onerror=alert(1)"' not in page.text
    assert "&lt;script&gt;" in page.text and "&quot;&gt;&lt;img" in page.text
    assert "default-src 'none'" in page.headers["Content-Security-Policy"]
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    # A forged link changes nothing and reflects nothing.
    forged = await who.http.post("/api/v1/email/unsubscribe", params={"u": user_id, "t": "tab", "s": attr})
    assert forged.status_code == 404 and is_json(forged) and "onerror" not in forged.text
    bad_uuid = await who.get("/api/v1/email/unsubscribe", params={"u": script, "t": "tab", "s": "x"})
    assert bad_uuid.status_code == 422 and is_json(bad_uuid)
    # The real one-click confirmation page reflects only our own copy.
    kind = next(iter(TYPE_LABELS))
    sig = unsubscribe_sig(settings.token_hash_pepper.get_secret_value(), uuid.UUID(user_id), kind)
    done = await who.http.post("/api/v1/email/unsubscribe", params={"u": user_id, "t": kind, "s": sig})
    assert done.status_code == 200 and "<script" not in done.text and TYPE_LABELS[kind] in done.text


FORMULAS = ["=1+2", "+1+2", "-1+2", "@SUM(1,2)", '=HYPERLINK("https://evil.test","x")', "\t=1+2", "\r=1+2"]


@tid("SEC-06")
@fr("FR-54")
async def test_SEC_06_csv_export_neutralizes_spreadsheet_formulas(api: ApiFactory) -> None:
    """Item and store names are typed by roommates; a cell starting with = + - @ (or a tab/CR before
    one) runs as a formula when the CSV is opened in Excel or Sheets (OWASP CSV injection)."""
    h = await build_house(api, receipts=False)
    payer = h.who[PAYER]
    bill = ok(await payer.post(f"/api/v1/houses/{h.house_id}/bills", {"store_name": "=cmd|' /C calc'!A0"}), 201)
    items = [
        ok(await payer.post(f"/api/v1/bills/{bill['id']}/items", {"name": f, "line_total_cents": 100}), 201)["id"]
        for f in FORMULAS
    ]
    changes = [{"item_id": i, "participant": h.key(h.payer_member), "on": True} for i in items]
    ok(await payer.call("PUT", f"/api/v1/bills/{bill['id']}/allocations", {"changes": changes}))
    export = await payer.get(f"/api/v1/bills/{bill['id']}/export.csv")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    assert export.headers["content-disposition"].startswith("attachment")
    rows = list(csv.reader(io.StringIO(export.text)))
    dangerous = [cell for row in rows[1:] for cell in row if _formula(cell)]
    assert not dangerous, f"CSV cells that a spreadsheet would run as formulas: {dangerous}"


def _formula(cell: str) -> bool:
    if not cell or cell[0] not in "=+-@\t\r":
        return False
    return not (cell[0] == "-" and cell[1:].replace(".", "", 1).isdigit())  # -1.25 is an amount
