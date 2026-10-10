"""Receipt pipeline end to end (Milestone 5; PRD §9; ADR-0010, ADR-0015): upload ticket → file
straight into storage → attach → text-PDF parsing or browser OCR → draft items.

Storage is real: MinIO (compose) with PROFILE=local / STORAGE_PROVIDER=s3, the in-repo fake Vercel
Blob service with PROFILE=vercel / STORAGE_PROVIDER=fakeblob. Nothing calls a real external service.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.extractor import FakeExtractor, build_plugin, image_key, plugin_enabled
from app.adapters.storage import (
    FakeBlobStorage,
    S3Storage,
    SigV4,
    Storage,
    storage_from_settings,
)
from app.errors import ErrorCode
from app.extraction.amounts import normalize_quantity
from app.extraction.evaluate import FIXTURES, IMAGE_LINES_PCT, _lcs_pairs, line_key, norm_name
from app.extraction.parser import PARSER_VERSION
from app.extraction.schema import ExtractedBill
from app.main import create_app
from app.models import HouseEvent, Receipt, ReceiptExtraction
from app.policy import Action, BillCtx, MemberCtx, decide
from app.services.maintenance import purge_receipt_files
from app.settings import Settings
from fakeservices import fake_blob
from tests.extraction.conftest import manifest, truth
from tests.harness.api import BASE_URL, Api
from tests.harness.servers import free_port, serve
from tests.harness.tags import fr, tid
from tests.test_bills import World, world

ApiFactory = Callable[[], Api]
FX = {fx["id"]: fx for fx in manifest()}
MIME = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".png": "image/png"}


# ---- storage per profile ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def blob_url(settings: Settings) -> Iterator[str | None]:
    """PROFILE=vercel: the fake Vercel Blob service on a free port (ADR-0017)."""
    if settings.profile != "vercel":
        yield None
        return
    port = free_port()
    with serve(fake_blob.create_app(f"http://127.0.0.1:{port}"), port) as url:
        yield url


@pytest.fixture
def storage_settings(settings: Settings, blob_url: str | None) -> Settings:
    if blob_url is None:
        return settings  # STORAGE_PROVIDER=s3 → the compose MinIO
    return settings.model_copy(update={"storage_provider": "fakeblob", "fake_blob_url": blob_url})


async def _running(settings: Settings, **state: Any) -> AsyncIterator[Any]:
    from fakeservices.mock_oidc import create_app as create_mock_oidc

    application = create_app(settings)
    application.state.google_transport = httpx.ASGITransport(app=create_mock_oidc(settings.google_issuer))
    async with application.router.lifespan_context(application):
        for k, v in state.items():
            setattr(application.state, k, v)
        yield application


@pytest.fixture
async def app(storage_settings: Settings) -> AsyncIterator[Any]:
    """Overrides conftest's `app` so the receipt routes use this profile's storage."""
    async for application in _running(storage_settings):
        yield application


@pytest.fixture
def storage(storage_settings: Settings) -> Storage:
    return storage_from_settings(storage_settings)


# ---- helpers --------------------------------------------------------------------------------------------


async def new_draft(w: World, **body: Any) -> str:
    r = await w.admin.post(f"/api/v1/houses/{w.hid}/bills", body)
    assert r.status_code == 201, r.text
    bill_id: str = r.json()["id"]
    return bill_id


async def upload(a: Api, bill_id: str, data: bytes, mime: str) -> dict[str, Any]:
    """What the browser does: ask for a ticket, then send the file straight to storage."""
    r = await a.post("/api/v1/uploads/authorize", {"bill_id": bill_id, "mime": mime, "bytes": len(data)})
    assert r.status_code == 200, r.text
    ticket: dict[str, Any] = r.json()
    assert ticket["storage_key"].startswith(f"receipts/{bill_id}/")
    assert ticket["method"] == "PUT" and ticket["upload_url"]
    async with httpx.AsyncClient() as c:
        put = await c.put(ticket["upload_url"], content=data, headers=ticket["headers"])
    assert put.status_code == 200, put.text
    return ticket


async def attach(a: Api, bill_id: str, ticket: dict[str, Any], data: bytes, mime: str, **kw: Any) -> httpx.Response:
    body = {"storage_key": ticket["storage_key"], "mime": mime, "bytes": len(data)}
    return await a.post(f"/api/v1/bills/{bill_id}/receipts", body, **kw)


async def upload_file(a: Api, bill_id: str, name: str) -> httpx.Response:
    data = (FIXTURES / name).read_bytes()
    mime = MIME[Path(name).suffix]
    return await attach(a, bill_id, await upload(a, bill_id, data, mime), data, mime)


def ocr_parts(fid: str) -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / f"{fid}.ocr.json").read_text(encoding="utf-8"))
    if payload.get("multi_photo"):
        return [{"pages": [p]} for p in payload["pages"]]
    return [{"pages": payload["pages"]}]


def score(view: dict[str, Any], t: ExtractedBill) -> tuple[int, int]:
    """Lines exactly right (evaluate.py's rule) between the saved draft and the truth file. The
    draft stores line totals after line discounts, so the discount is added back."""
    truth_keys = [line_key(i) for i in t.items]
    got = [
        (
            norm_name(i["name"]),
            i["line_total_cents"] + i["discount_cents"],
            normalize_quantity(Decimal(i["quantity"])),
        )
        for i in view["items"]
    ]
    return len(_lcs_pairs(truth_keys, got)), max(len(truth_keys), len(got))


def pdf_bytes(pages: int, password: str | None = None) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


async def extractions(sessions: async_sessionmaker[AsyncSession], bill_id: str) -> list[ReceiptExtraction]:
    async with sessions() as db:
        return list(
            (
                await db.scalars(
                    select(ReceiptExtraction)
                    .where(ReceiptExtraction.bill_id == uuid.UUID(bill_id))
                    .order_by(ReceiptExtraction.created_at)
                )
            ).all()
        )


async def count(sessions: async_sessionmaker[AsyncSession], model: Any, **where: Any) -> int:
    async with sessions() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return int(await db.scalar(q) or 0)


# ---- storage adapters ---------------------------------------------------------------------------------


@tid("EXTRACT-01")
@fr("FR-10")
def test_EXTRACT_01_sigv4_matches_the_aws_presigned_url_example() -> None:
    """AWS's documented query-string example (GET examplebucket/test.txt, 2013-05-24, 86400 s)."""
    signer = SigV4("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1")
    when = datetime(2013, 5, 24, tzinfo=UTC)
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": "AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request",
        "X-Amz-Date": "20130524T000000Z",
        "X-Amz-Expires": "86400",
        "X-Amz-SignedHeaders": "host",
    }
    _, sig = signer.signature(
        method="GET",
        path="/test.txt",
        query=query,
        headers={"host": "examplebucket.s3.amazonaws.com"},
        payload_hash="UNSIGNED-PAYLOAD",
        when=when,
    )
    assert sig == "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"


@tid("EXTRACT-01", "EXTRACT-10")
@fr("FR-10", "FR-18")
async def test_EXTRACT_01_storage_contract(storage: Storage, storage_settings: Settings) -> None:
    """The adapter this profile uses: ticket upload, head, ranged get, private view URL, put, delete."""
    assert storage.provider == ("fakeblob" if storage_settings.profile == "vercel" else "s3")
    key = f"receipts/{uuid.uuid4()}/{'k' * 32}.jpg"
    data = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4
    ticket = await storage.upload_ticket(key, "image/jpeg", len(data))
    assert ticket.upload_url
    async with httpx.AsyncClient() as c:
        # a different size than authorized is refused by storage itself
        assert (await c.put(ticket.upload_url, content=data + b"x", headers=ticket.headers)).status_code >= 400
        assert (await c.put(ticket.upload_url, content=data, headers=ticket.headers)).status_code == 200
        info = await storage.head(key)
        assert info is not None and info.size == len(data)
        assert await storage.get(key, first_bytes=4) == data[:4]
        assert await storage.get(key) == data
        assert (await c.get(ticket.upload_url.split("?")[0])).status_code in (401, 403)  # private
        view = await c.get(await storage.view_url(key))
        assert view.status_code == 200 and view.content == data
        assert view.headers["content-disposition"].startswith("attachment")  # SEC-04 shape
    await storage.put(f"backups/{uuid.uuid4()}.bin", b"export", "application/octet-stream")
    await storage.delete(key)
    assert await storage.head(key) is None
    if isinstance(storage, S3Storage):
        assert "X-Amz-Signature=" in ticket.upload_url and "content-length" in ticket.upload_url.lower()
    else:
        assert isinstance(storage, FakeBlobStorage) and ticket.blob and ticket.blob["client_token"]


# ---- text PDFs ------------------------------------------------------------------------------------------


@tid("EXTRACT-11", "EXTRACT-01", "EXTRACT-04", "EXTRACT-16", "EXTRACT-13")
@fr("FR-10", "FR-11", "FR-13", "FR-16")
@pytest.mark.parametrize("fid", ["r01", "r06", "r09", "r12"])
async def test_EXTRACT_11_text_pdf_through_the_ticket_becomes_draft_items(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession], fid: str
) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    t = truth(fid)
    resp = await upload_file(w.admin, bill_id, FX[fid]["files"][0])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["extraction"] == "done" and body["receipt"]["mime"] == "application/pdf"
    assert body["bill"]["extraction_status"] == "done"
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    right, denom = score(view, t)
    assert right == denom == len(t.items), "text PDFs: every line exactly right"
    bill = view["bill"]
    assert bill["receipt_total_cents"] == t.total_cents  # the printed total, 100% right
    assert bill["tax_cents"] == (t.tax_cents or 0)
    assert t.purchase_date is not None
    assert bill["store_name"] == t.store_name and bill["purchase_date"] == t.purchase_date.isoformat()
    assert sorted((f["kind"], f["amount_cents"]) for f in view["fees"]) == sorted(
        (f.kind, f.amount_cents) for f in t.fees
    )
    recon = view["reconciliation"]
    assert recon["computed_cents"] == t.computed_total_cents  # recomputed by the server (EXTRACT-04)
    if t.mismatch_cents is None:
        assert recon["ok"] and bill["total_cents"] == t.total_cents
    else:  # EXTRACT-16: the printed total disagrees and review shows it
        assert not recon["ok"] and recon["difference_cents"] == t.mismatch_cents
    assert all(i["raw_text"] for i in view["items"])  # kept for review next to the cleaned name
    [ex] = await extractions(sessions, bill_id)
    assert (ex.model, ex.prompt_version, ex.status) == ("local-parser", PARSER_VERSION, "done")
    assert ex.raw_json is not None and len(ex.raw_json["items"]) == len(t.items)
    assert ex.latency_ms is not None and ex.latency_ms >= 0
    async with sessions() as db:
        types = (
            await db.scalars(
                select(HouseEvent.type).where(HouseEvent.house_id == uuid.UUID(w.hid)).order_by(HouseEvent.seq)
            )
        ).all()
    assert types[-2:] == ["receipt_added", "extraction_applied"]
    listed = (await w.admin.get(f"/api/v1/bills/{bill_id}/receipts")).json()
    assert [r["id"] for r in listed] == [body["receipt"]["id"]]


@tid("EXTRACT-11", "SYNC-15")
@fr("FR-11", "FR-14", "FR-15", "FR-16")
async def test_EXTRACT_11_scan_never_overwrites_hand_typed_items_it_is_offered(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id = await new_draft(w, store_name="Our pick", purchase_date="2026-09-30")
    typed = await w.admin.post(f"/api/v1/bills/{bill_id}/items", {"name": "Birthday candles", "line_total_cents": 299})
    assert typed.status_code == 201
    resp = await upload_file(w.admin, bill_id, "r01.pdf")
    assert resp.status_code == 201 and resp.json()["extraction"] == "offered"
    t = truth("r01")
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert [i["name"] for i in view["items"]] == ["Birthday candles"]
    assert view["bill"]["receipt_total_cents"] is None and view["bill"]["tax_cents"] == 0  # nothing applied
    offer = view["scanned_offer"]
    assert offer["item_count"] == len(t.items) and offer["total_cents"] == t.total_cents
    assert offer["extraction_id"] == resp.json()["result"]["extraction_id"]
    # dismiss: the typed items stay and the banner goes away
    dismissed = await w.admin.post(f"/api/v1/bills/{bill_id}/extractions/{offer['extraction_id']}/dismiss")
    assert dismissed.status_code == 200 and dismissed.json()["extraction"] == "dismissed"
    assert (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["scanned_offer"] is None
    gone = await w.admin.post(f"/api/v1/bills/{bill_id}/extractions/{offer['extraction_id']}/apply")
    assert gone.status_code == 409
    # a newer scan is offered again and replaces any older offer; accepting it replaces the items
    first = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"parts": ocr_parts("i02")})
    second = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"parts": ocr_parts("i01")})
    assert first.json()["extraction"] == second.json()["extraction"] == "offered"
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert view["scanned_offer"]["extraction_id"] == second.json()["result"]["extraction_id"]
    stale = await w.admin.post(f"/api/v1/bills/{bill_id}/extractions/{first.json()['result']['extraction_id']}/apply")
    assert stale.status_code == 409
    url = f"/api/v1/bills/{bill_id}/extractions/{second.json()['result']['extraction_id']}/apply"
    accepted = await w.admin.post(url, key="accept-1")
    assert accepted.status_code == 200 and accepted.json()["extraction"] == "done"
    replay = await w.admin.post(url, key="accept-1")
    assert replay.headers.get("idempotent-replayed") == "true" and replay.json() == accepted.json()
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert "Birthday candles" not in [i["name"] for i in view["items"]]
    right, denom = score(view, truth("i01"))
    assert right * 100 >= IMAGE_LINES_PCT * denom
    # the payer's own header choices win over the receipt's
    assert view["bill"]["store_name"] == "Our pick" and view["bill"]["purchase_date"] == "2026-09-30"
    assert view["bill"]["receipt_total_cents"] == truth("i01").total_cents and view["scanned_offer"] is None
    statuses = sorted((e.status, e.error or "") for e in await extractions(sessions, bill_id))
    assert statuses == [("done", ""), ("failed", "dismissed"), ("failed", "superseded")]
    # editing one scanned line by hand also protects the draft from the next scan
    item = view["items"][0]
    edited = await w.admin.call(
        "PATCH", f"/api/v1/bills/{bill_id}/items/{item['id']}", {"name": "Milk (ours)"}, if_match=item["version"]
    )
    assert edited.status_code == 200
    again = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"parts": ocr_parts("i01")})
    assert again.json()["extraction"] == "offered"
    assert (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()["items"][0]["name"] == "Milk (ours)"


@tid("SYNC-15")
@fr("FR-11", "FR-14")
async def test_SYNC_15_manual_item_added_while_ocr_is_in_flight_stays(
    api: ApiFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The payer types an item while the browser's OCR result is being parsed: the typed item stays
    and the scan is offered ("Use the scanned items instead?"), never silently applied."""
    from app.services import receipts as receipt_svc

    w = await world(api)
    bill_id = await new_draft(w)
    receipt = (await upload_file(w.admin, bill_id, "i01.jpg")).json()["receipt"]
    entered, release = threading.Event(), threading.Event()
    real = receipt_svc.parse_ocr

    def slow_parse(parts: Any) -> Any:
        entered.set()
        assert release.wait(30)
        return real(parts)

    monkeypatch.setattr(receipt_svc, "parse_ocr", slow_parse)
    ocr = asyncio.create_task(
        w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"receipt_id": receipt["id"], "parts": ocr_parts("i01")})
    )
    assert await asyncio.to_thread(entered.wait, 30)
    typed = await w.admin.post(f"/api/v1/bills/{bill_id}/items", {"name": "Napkins", "line_total_cents": 199})
    assert typed.status_code == 201
    release.set()
    result = await ocr
    assert result.status_code == 200, result.text
    assert result.json()["extraction"] == "offered"
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert [i["name"] for i in view["items"]] == ["Napkins"]
    assert view["scanned_offer"]["item_count"] == len(truth("i01").items)
    assert view["bill"]["extraction_status"] == "done"


# ---- photos and scanned PDFs: needs_ocr, then the browser's OCR --------------------------------------------


@tid("EXTRACT-03", "EXTRACT-15", "EXTRACT-05", "EXTRACT-16")
@fr("FR-10", "FR-11", "FR-12", "FR-13")
@pytest.mark.parametrize("fid", ["i01", "i05", "i08", "i09"])
async def test_EXTRACT_03_image_needs_ocr_then_ocr_lines_fill_the_draft(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession], fid: str
) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    resp = await upload_file(w.admin, bill_id, FX[fid]["files"][0])
    assert resp.status_code == 201, resp.text
    attached = resp.json()
    # no EXTRACTOR_API_KEY: the server never sees pixels through a model; the browser OCRs (EXTRACT-15)
    assert attached["extraction"] == "needs_ocr" and attached["bill"]["extraction_status"] == "pending"
    assert attached["result"] is None
    ocr = await w.admin.post(
        f"/api/v1/bills/{bill_id}/ocr", {"receipt_id": attached["receipt"]["id"], "parts": ocr_parts(fid)}
    )
    assert ocr.status_code == 200, ocr.text
    assert ocr.json()["extraction"] == "done" and ocr.json()["result"]["source"] == "ocr"
    t = truth(fid)
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    right, denom = score(view, t)
    assert right * 100 >= IMAGE_LINES_PCT * denom, f"{right}/{denom} lines right"
    assert view["bill"]["receipt_total_cents"] == t.total_cents
    assert view["bill"]["extraction_status"] == "done"
    flagged = [i for i in view["items"] if i["flagged"]]
    assert all(Decimal(i["confidence"]) < Decimal("0.8") for i in flagged)  # "CHECK?" chip (FR-12)
    if t.mismatch_cents is not None:
        assert view["reconciliation"]["difference_cents"] == t.mismatch_cents and not view["reconciliation"]["ok"]
    [ex] = await extractions(sessions, bill_id)
    assert ex.model == "local-parser" and ex.raw_json is not None and ex.raw_json["source"] == "ocr"


@tid("EXTRACT-08", "EDGE-15-02")
@fr("FR-11")
async def test_EXTRACT_08_three_photos_of_one_long_receipt_merge_without_duplicates(api: ApiFactory) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    receipts = []
    for name in FX["i10"]["files"]:
        resp = await upload_file(w.admin, bill_id, name)
        assert resp.status_code == 201 and resp.json()["extraction"] == "needs_ocr"
        receipts.append(resp.json()["receipt"])
    assert [r["page_index"] for r in receipts] == [0, 1, 2]
    ocr = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"parts": ocr_parts("i10")})
    assert ocr.status_code == 200, ocr.text
    assert any(x.startswith("overlap_deduped") for x in ocr.json()["result"]["warnings"])
    t = truth("i10")
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    right, denom = score(view, t)
    assert right * 100 >= IMAGE_LINES_PCT * denom
    assert len(view["items"]) == len(t.items), "overlapping lines were not double counted"
    assert view["bill"]["receipt_total_cents"] == t.total_cents


# ---- refusals ------------------------------------------------------------------------------------------------


@tid("EXTRACT-01")
@fr("FR-10")
async def test_EXTRACT_01_wrong_magic_bytes_are_415_and_the_file_is_removed(
    api: ApiFactory, storage: Storage, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    png = (FIXTURES / "i03.png").read_bytes()[:4096]
    for data, mime in (
        (b"just some text, not a photo at all", "image/jpeg"),
        (png, "image/jpeg"),
        (png, "application/pdf"),
    ):
        ticket = await upload(w.admin, bill_id, data, mime)
        resp = await attach(w.admin, bill_id, ticket, data, mime)
        assert resp.status_code == 415, resp.text
        assert resp.json()["code"] == ErrorCode.UNSUPPORTED_MEDIA
        assert await storage.head(ticket["storage_key"]) is None
    assert await count(sessions, Receipt) == 0
    gif = await w.admin.post("/api/v1/uploads/authorize", {"bill_id": bill_id, "mime": "image/gif", "bytes": 10})
    assert gif.status_code == 415
    # a key from another bill, or one the API didn't shape, is refused
    other = await new_draft(w)
    data = (FIXTURES / "i01.jpg").read_bytes()
    ticket = await upload(w.admin, other, data, "image/jpeg")
    assert (await attach(w.admin, bill_id, ticket, data, "image/jpeg")).status_code == 422
    # attaching before the upload finished
    missing = {"storage_key": f"receipts/{bill_id}/{'m' * 32}.jpg", "mime": "image/jpeg", "bytes": 10}
    assert (await w.admin.post(f"/api/v1/bills/{bill_id}/receipts", missing)).status_code == 409


@tid("EXTRACT-01")
@fr("FR-10")
async def test_EXTRACT_01_over_20_mb_is_413_before_upload(api: ApiFactory) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    over = 20 * 1024 * 1024 + 1
    for body in (
        {"bill_id": bill_id, "mime": "image/jpeg", "bytes": over},
        {"bill_id": bill_id, "mime": "application/pdf", "bytes": 1000, "pages": 11},
    ):
        resp = await w.admin.post("/api/v1/uploads/authorize", body)
        assert resp.status_code == 413 and resp.json()["code"] == ErrorCode.FILE_TOO_LARGE
    big = {"storage_key": f"receipts/{bill_id}/{'b' * 32}.jpg", "mime": "image/jpeg", "bytes": over}
    assert (await w.admin.post(f"/api/v1/bills/{bill_id}/receipts", big)).status_code == 413
    ok = await w.admin.post(
        "/api/v1/uploads/authorize", {"bill_id": bill_id, "mime": "image/heic", "bytes": 20 * 1024 * 1024}
    )
    assert ok.status_code == 200 and ok.json()["storage_key"].endswith(".heic")


@tid("EXTRACT-01", "EXTRACT-11")
@fr("FR-10")
async def test_EXTRACT_01_eleven_page_and_locked_pdfs_are_refused(
    api: ApiFactory, storage: Storage, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    for data, status, title in ((pdf_bytes(11), 413, "10 pages"), (pdf_bytes(1, "pw"), 422, "password")):
        ticket = await upload(w.admin, bill_id, data, "application/pdf")
        resp = await attach(w.admin, bill_id, ticket, data, "application/pdf")
        assert resp.status_code == status, resp.text
        assert title in resp.json()["title"]
        assert await storage.head(ticket["storage_key"]) is None
    assert await count(sessions, Receipt) == 0
    # a 10-page scan is fine: it just needs the browser's OCR
    ten = pdf_bytes(10)
    resp = await attach(
        w.admin, bill_id, await upload(w.admin, bill_id, ten, "application/pdf"), ten, "application/pdf"
    )
    assert resp.status_code == 201 and resp.json()["extraction"] == "needs_ocr"


# ---- privacy and permissions ---------------------------------------------------------------------------------


@tid("EXTRACT-10", "SEC-02", "EDGE-15-07")
@fr("FR-18")
async def test_EXTRACT_10_receipt_files_only_via_short_lived_urls_to_members(api: ApiFactory) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    data = (FIXTURES / "i01.jpg").read_bytes()
    receipt = (await upload_file(w.admin, bill_id, "i01.jpg")).json()["receipt"]
    url = f"/api/v1/receipts/{receipt['id']}/view"
    mine = await w.admin.get(url)
    assert mine.status_code == 200 and mine.headers["cache-control"] == "private, no-store"
    assert datetime.fromisoformat(mine.json()["expires_at"]) <= datetime.now(UTC) + timedelta(minutes=6)
    async with httpx.AsyncClient() as c:
        got = await c.get(mine.json()["url"])
    assert got.status_code == 200 and got.content == data
    # another member of the house can look (not only the payer)
    arjun = api()
    await arjun.sign_in("arjun@example.com")
    claimed = await arjun.post(f"/api/v1/join/{w.house['invite_token']}/claim", {"member_id": w.member["arjun"]})
    assert claimed.status_code == 200
    assert (await arjun.get(url)).status_code == 200
    assert (await arjun.get(f"/api/v1/bills/{bill_id}/receipts")).status_code == 200
    # ...but only whoever may edit the draft can upload to it
    denied = await arjun.post("/api/v1/uploads/authorize", {"bill_id": bill_id, "mime": "image/jpeg", "bytes": 10})
    assert denied.status_code == 403
    # someone from another house gets nothing
    stranger = api()
    await stranger.sign_in("stranger@example.com")
    await stranger.create_house("Elsewhere")
    resp = await stranger.get(url)
    assert resp.status_code == 403 and resp.json()["code"] == ErrorCode.NOT_A_MEMBER
    assert (await stranger.get(f"/api/v1/bills/{bill_id}/receipts")).status_code == 403
    sneaky = await stranger.post("/api/v1/uploads/authorize", {"bill_id": bill_id, "mime": "image/jpeg", "bytes": 10})
    assert sneaky.status_code == 403
    assert (await api().get(url)).status_code == 401
    assert (await w.admin.get(f"/api/v1/receipts/{uuid.uuid4()}/view")).status_code == 404


@tid("EXTRACT-10", "EXTRACT-01")
@fr("FR-18", "FR-10")
def test_EXTRACT_10_policy_rows_for_receipts() -> None:
    payer = MemberCtx(uuid.uuid4(), uuid.uuid4(), "admin", "active")
    other = MemberCtx(uuid.uuid4(), uuid.uuid4(), "member", "active")
    left = MemberCtx(uuid.uuid4(), uuid.uuid4(), "member", "left")

    def bill(state: str) -> BillCtx:
        return BillCtx(payer.member_id, payer.user_id, state)

    assert decide(Action.RECEIPT_UPLOAD, actor=payer, bill=bill("draft")).allowed
    assert decide(Action.RECEIPT_UPLOAD, actor=other, bill=bill("draft")).code == ErrorCode.FORBIDDEN
    assert decide(Action.RECEIPT_UPLOAD, actor=payer, bill=bill("open")).code == ErrorCode.CONFLICT
    assert decide(Action.RECEIPT_UPLOAD, actor=payer, bill=bill("settled")).code == ErrorCode.BILL_SETTLED
    assert decide(Action.RECEIPT_UPLOAD, actor=None, bill=bill("draft")).code == ErrorCode.NOT_A_MEMBER
    assert decide(Action.RECEIPT_VIEW, actor=other).allowed
    assert not decide(Action.RECEIPT_VIEW, actor=left).allowed
    assert not decide(Action.RECEIPT_VIEW, actor=None).allowed


# ---- write discipline ---------------------------------------------------------------------------------------


@tid("EXTRACT-01", "SYNC-28", "SYNC-29")
@fr("FR-10", "FR-11")
async def test_EXTRACT_01_attach_and_ocr_replay_by_idempotency_key(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    data = (FIXTURES / "i01.jpg").read_bytes()
    ticket = await upload(w.admin, bill_id, data, "image/jpeg")
    first = await attach(w.admin, bill_id, ticket, data, "image/jpeg", key="attach-1")
    assert first.status_code == 201
    events = await count(sessions, HouseEvent)
    again = await attach(w.admin, bill_id, ticket, data, "image/jpeg", key="attach-1")
    assert again.status_code == 201 and again.headers.get("idempotent-replayed") == "true"
    assert again.json() == first.json()
    assert await count(sessions, Receipt) == 1 and await count(sessions, HouseEvent) == events
    # same key, different body
    clash = await w.admin.post(
        f"/api/v1/bills/{bill_id}/receipts",
        {"storage_key": ticket["storage_key"], "mime": "image/jpeg", "bytes": len(data) + 1},
        key="attach-1",
    )
    assert clash.status_code == 422 and clash.json()["code"] == ErrorCode.IDEMPOTENCY_MISMATCH
    # a new key for the same file: it's already attached
    assert (await attach(w.admin, bill_id, ticket, data, "image/jpeg")).status_code == 409
    no_key = await w.admin.call(
        "POST",
        f"/api/v1/bills/{bill_id}/receipts",
        {"storage_key": ticket["storage_key"], "mime": "image/jpeg", "bytes": len(data)},
        idem=False,
    )
    assert no_key.status_code == 400 and no_key.json()["code"] == ErrorCode.IDEMPOTENCY_KEY_REQUIRED

    body = {"receipt_id": first.json()["receipt"]["id"], "parts": ocr_parts("i01")}
    one = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", body, key="ocr-1")
    assert one.status_code == 200
    events = await count(sessions, HouseEvent)
    two = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", body, key="ocr-1")
    assert two.status_code == 200 and two.headers.get("idempotent-replayed") == "true" and two.json() == one.json()
    assert await count(sessions, HouseEvent) == events
    assert len(await extractions(sessions, bill_id)) == 1
    no_ocr_key = await w.admin.call("POST", f"/api/v1/bills/{bill_id}/ocr", body, idem=False)
    assert no_ocr_key.status_code == 400


@tid("EXTRACT-06", "EDGE-15-01")
@fr("FR-15", "FR-11")
async def test_EXTRACT_06_failed_extraction_keeps_the_file_and_allows_retry_or_manual(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    w = await world(api)
    bill_id = await new_draft(w)
    receipt = (await upload_file(w.admin, bill_id, "i07.jpg")).json()["receipt"]
    junk = {
        "pages": [
            {
                "page": 1,
                "lines": [
                    {"text": "a blurry photo of a cat", "bbox": [0, 0, 300, 20], "confidence": 31},
                    {"text": "~~ ## ~~", "bbox": [0, 30, 100, 50], "confidence": 12.5},
                ],
            }
        ]
    }
    failed = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"receipt_id": receipt["id"], "parts": [junk]})
    assert failed.status_code == 200, failed.text
    assert failed.json()["extraction"] == "failed" and failed.json()["bill"]["extraction_status"] == "failed"
    view = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert view["items"] == [] and view["bill"]["extraction_status"] == "failed"  # nothing invented
    assert [r["id"] for r in (await w.admin.get(f"/api/v1/bills/{bill_id}/receipts")).json()] == [receipt["id"]]
    [ex] = await extractions(sessions, bill_id)
    assert ex.status == "failed" and ex.error
    # "Enter by hand" works on the same draft, and "Try again" with a better photo's OCR too
    manual = await w.admin.post(f"/api/v1/bills/{bill_id}/items", {"name": "Milk", "line_total_cents": 429})
    assert manual.status_code == 201
    retry = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"receipt_id": receipt["id"], "parts": ocr_parts("i07")})
    assert retry.json()["extraction"] == "offered"  # the typed item stays; the scan is offered (SYNC-15)
    after = (await w.admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert after["bill"]["extraction_status"] == "done" and [i["name"] for i in after["items"]] == ["Milk"]
    # malformed OCR payloads are refused by validation, not stored
    bad = await w.admin.post(f"/api/v1/bills/{bill_id}/ocr", {"parts": [{"pages": [{"lines": [{"text": "x"}]}]}]})
    assert bad.status_code == 422
    # a receipt id from another tab is refused
    other = await new_draft(w)
    wrong = await w.admin.post(f"/api/v1/bills/{other}/ocr", {"receipt_id": receipt["id"], "parts": ocr_parts("i07")})
    assert wrong.status_code == 404


# ---- the optional LLM plugin ------------------------------------------------------------------------------------


@pytest.fixture
async def plugin_app(
    storage_settings: Settings, tmp_path: Path, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Any]:
    """EXTRACTOR_API_KEY set: the plugin runs, backed by the FakeExtractor (never a real model)."""
    cfg = storage_settings.model_copy(
        update={"extractor_provider": "fake", "extractor_api_key": "test-only-key", "extractor_daily_cap_per_house": 30}
    )
    i01 = (FIXTURES / "i01.jpg").read_bytes()
    (tmp_path / f"{image_key([i01])}.json").write_text(
        truth("i01").model_dump_json(exclude={"source", "computed_total_cents", "mismatch_cents"}), encoding="utf-8"
    )
    async for application in _running(cfg, extractor=FakeExtractor(tmp_path)):
        yield application


def _client(application: Any) -> Api:
    return Api(httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=BASE_URL))


@tid("EXTRACT-09", "EXTRACT-15")
@fr("FR-11")
async def test_EXTRACT_09_llm_plugin_cap_is_30_per_house_per_day(plugin_app: Any) -> None:
    admin = _client(plugin_app)
    await admin.sign_in("pranav@example.com")
    w = World(admin, await admin.create_house())
    bill_id = await new_draft(w)
    data = (FIXTURES / "i01.jpg").read_bytes()
    for n in range(30):
        resp = await attach(admin, bill_id, await upload(admin, bill_id, data, "image/jpeg"), data, "image/jpeg")
        assert resp.status_code == 201, (n, resp.text)
        assert resp.json()["extraction"] == "done" and resp.json()["result"]["source"] == "llm"
    ticket = await upload(admin, bill_id, data, "image/jpeg")
    over = await attach(admin, bill_id, ticket, data, "image/jpeg")
    assert over.status_code == 429 and over.json()["code"] == ErrorCode.RATE_LIMITED
    assert int(over.headers["retry-after"]) > 0
    # text PDFs never use the plugin, so they aren't capped
    assert (await upload_file(admin, bill_id, "r01.pdf")).json()["extraction"] == "done"
    plugin_app.state.clock.advance(timedelta(days=1))
    assert (await attach(admin, bill_id, ticket, data, "image/jpeg")).status_code == 201
    view = (await admin.get(f"/api/v1/bills/{bill_id}")).json()
    assert len(view["items"]) == len(truth("i01").items)
    await admin.http.aclose()


@tid("EXTRACT-15", "EXTRACT-06", "EDGE-15-27")
@fr("FR-11")
async def test_EXTRACT_15_plugin_only_with_a_key_and_failures_fall_back(
    plugin_app: Any, storage_settings: Settings
) -> None:
    assert not plugin_enabled(storage_settings)  # the test env: EXTRACTOR_PROVIDER=fake, no key
    assert build_plugin(storage_settings) is None
    assert (
        build_plugin(storage_settings.model_copy(update={"extractor_provider": "local", "extractor_api_key": "k"}))
        is None
    )
    keyed = storage_settings.model_copy(update={"extractor_api_key": "k"})
    assert plugin_enabled(keyed) and isinstance(build_plugin(keyed), FakeExtractor)
    admin = _client(plugin_app)
    await admin.sign_in("pranav@example.com")
    w = World(admin, await admin.create_house())
    bill_id = await new_draft(w)
    resp = await upload_file(admin, bill_id, "i02.jpg")  # no fixture for these pixels: the plugin fails
    assert resp.status_code == 201 and resp.json()["extraction"] == "failed"
    assert resp.json()["bill"]["extraction_status"] == "failed"
    assert len((await admin.get(f"/api/v1/bills/{bill_id}/receipts")).json()) == 1  # the file stays
    ocr = await admin.post(f"/api/v1/bills/{bill_id}/ocr", {"parts": ocr_parts("i02")})
    assert ocr.json()["extraction"] == "done"  # browser OCR is always there as the fallback
    await admin.http.aclose()


# ---- retention ---------------------------------------------------------------------------------------------


@tid("DATA-03")
@fr("FR-18")
async def test_DATA_03_receipt_files_deleted_two_years_after_settle(
    api: ApiFactory, app: Any, storage: Storage, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    w = await world(api)
    first, second = await new_draft(w), await new_draft(w)
    r1 = (await upload_file(w.admin, first, "i01.jpg")).json()["receipt"]
    r2 = (await upload_file(w.admin, second, "i03.png")).json()["receipt"]
    settled = datetime(2026, 10, 9, 12, tzinfo=UTC)
    async with sessions() as db, db.begin():
        for bill_id, when in ((first, settled), (second, settled + timedelta(days=30))):
            await db.execute(
                text("UPDATE bills SET state = 'settled', sent_at = :t, settled_at = :t WHERE id = :id"),
                {"t": when, "id": uuid.UUID(bill_id)},
            )

    async def purge(now: datetime) -> int:
        async with sessions() as db, db.begin():
            return await purge_receipt_files(db, now, storage)

    keys: dict[str, str] = {}
    async with sessions() as db:
        for r in (r1, r2):
            keys[r["id"]] = str(await db.scalar(select(Receipt.storage_key).where(Receipt.id == uuid.UUID(r["id"]))))
    assert await purge(settled + timedelta(days=729)) == 0
    assert await storage.head(keys[r1["id"]]) is not None
    assert await purge(settled.replace(year=2028) + timedelta(days=1)) == 1
    assert await storage.head(keys[r1["id"]]) is None
    assert await storage.head(keys[r2["id"]]) is not None
    gone = await w.admin.get(f"/api/v1/receipts/{r1['id']}/view")
    assert gone.status_code == 410
    assert await purge(settled.replace(year=2028) + timedelta(days=1)) == 0  # idempotent
    # the daily cron does the same with the app's clock
    app.state.clock.set(settled.replace(year=2028) + timedelta(days=31))
    assert settings.cron_secret is not None
    cron = await w.admin.get(
        "/api/v1/cron/daily", headers={"Authorization": f"Bearer {settings.cron_secret.get_secret_value()}"}
    )
    assert cron.status_code == 200, cron.text
    assert cron.json()["purged"]["receipt_files"] == 1
    assert await storage.head(keys[r2["id"]]) is None
    async with sessions() as db:
        rows = (await db.scalars(select(Receipt).order_by(Receipt.created_at))).all()
    assert all(r.deleted_at is not None for r in rows), "rows stay for history, files are gone"
