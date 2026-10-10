"""Receipts: upload tickets, attaching stored files, extraction applied to the draft (PRD §9, §10;
FR-10..FR-12, FR-18; ADR-0010, ADR-0015).

    POST /uploads/authorize      policy check → upload ticket (presigned PUT / Blob client upload)
    POST /bills/{id}/receipts    stored file → `receipts` row; text PDF parsed here, otherwise
                                 `needs_ocr` (the browser OCRs) or the optional LLM plugin
    POST /bills/{id}/ocr         browser OCR lines → parser → draft
    POST /bills/{id}/extractions/{eid}/apply|dismiss   the "Use the scanned items instead?" offer
    GET  /receipts/{id}/view     short-lived URL for house members

Write kinds (PRD §21.3): attaching and OCR are creates (Idempotency-Key, X-Client-Mutation-Id);
apply/dismiss of an offer are transitions. Slow work (storage reads, PDF text, parsing) happens
before the transaction; the write itself is one `house_write` (bill row locked first, house
sequence last). A request that already completed is answered from the idempotency store before
any of that work is repeated.

Scanned results are written to the draft only while nobody has typed or edited items by hand
(PRD §21.6, SYNC-15): applying replaces the draft's items, fills store name and date when the payer
hasn't, sets tax, fees (tip as a fee) and the printed total, and records a `receipt_extractions`
row. Otherwise the result is stored on that row and offered; the payer's items stay until they
accept it. The server recomputes every total (EXTRACT-04).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import ratelimit
from app.adapters.extractor import PLUGIN_TIMEOUT_SECONDS, ExtractionFailed, ReceiptExtractor
from app.adapters.storage import VIEW_TTL, Storage, StorageError, new_object_token
from app.domain.kitchen import storage_for
from app.errors import ErrorCode, Problem
from app.extraction.lines import Line, lines_from_ocr, merge_parts
from app.extraction.parser import PARSER_VERSION, parse_receipt
from app.extraction.pdf_text import PdfRejected, extract_pdf_lines
from app.extraction.schema import ExtractedBill
from app.ids import uuid7
from app.models import Bill, BillFee, House, Receipt, ReceiptExtraction
from app.models import Item as ItemRow
from app.models.enums import MAX_BILL_TOTAL_CENTS, MAX_ITEMS_PER_BILL
from app.policy import Action, require
from app.services import bill_math
from app.services.bills import _actor, _lock_bill, _refresh_totals, _today, bill_ctx
from app.services.house_write import (
    IDEMPOTENCY_TTL,
    Activity,
    HouseWrite,
    Idempotency,
    IdempotentReplay,
    WriteContext,
    house_write,
)
from app.services.houses import membership

log = logging.getLogger(__name__)

MAX_BYTES = 20 * 1024 * 1024  # FR-10; equals the receipts.bytes CHECK
MAX_PDF_PAGES = 10
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/heic": ".heic",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
PDF = "application/pdf"
KEY_RE = re.compile(
    r"^receipts/(?P<bill>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
    r"[A-Za-z0-9_-]{32,64}(?P<ext>\.(?:jpg|png|heic|webp|pdf))$"
)
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
LOCAL_MODEL = "local-parser"
SNIFF_BYTES = 1024

Kind = Literal["done", "needs_ocr", "failed", "offered"]
# receipt_extractions.status has no "offered" value (its CHECK predates the offer); a stored scan
# waiting for the payer's decision is "pending"; accepted -> "done"; dismissed or superseded -> "failed".
OFFERED = "pending"


# ---- checks ---------------------------------------------------------------------------------------


def check_file(mime: str, size: int, pages: int | None = None) -> None:
    """Refuse before upload (EXTRACT-01): type 415, over 20 MB or 10 PDF pages 413."""
    if mime not in EXTENSIONS:
        raise Problem(
            415,
            ErrorCode.UNSUPPORTED_MEDIA,
            "That file type isn't supported",
            "Use a photo (JPEG, PNG, HEIC, WEBP) or a PDF.",
        )
    if size <= 0:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "That file is empty")
    if size > MAX_BYTES:
        raise Problem(413, ErrorCode.FILE_TOO_LARGE, "That file is over 20 MB", "Try a smaller photo or PDF.")
    if mime == PDF and pages is not None and pages > MAX_PDF_PAGES:
        raise Problem(413, ErrorCode.FILE_TOO_LARGE, "That PDF has more than 10 pages")


def sniff(head: bytes) -> str | None:
    """The real type from magic bytes (never trust the declared type or the extension)."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS:
        return "image/heic"
    if b"%PDF-" in head[:SNIFF_BYTES]:
        return PDF
    return None


def new_key(bill_id: uuid.UUID, mime: str) -> str:
    """Unguessable, under the bill: `receipts/<bill id>/<192 random bits>.<ext>`."""
    return f"receipts/{bill_id}/{new_object_token()}{EXTENSIONS[mime]}"


def check_key(key: str, bill_id: uuid.UUID, mime: str) -> None:
    m = KEY_RE.match(key)
    if m is None or m["bill"] != str(bill_id) or m["ext"] != EXTENSIONS[mime]:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "That upload doesn't belong to this tab")


PDF_PROBLEMS: dict[str, tuple[int, ErrorCode, str]] = {
    "too_many_pages": (413, ErrorCode.FILE_TOO_LARGE, "That PDF has more than 10 pages"),
    "too_large": (413, ErrorCode.FILE_TOO_LARGE, "That file is over 20 MB"),
    "not_pdf": (415, ErrorCode.UNSUPPORTED_MEDIA, "That file isn't a PDF"),
    "encrypted": (422, ErrorCode.VALIDATION_FAILED, "That PDF is password-protected"),
    "unreadable": (422, ErrorCode.VALIDATION_FAILED, "That PDF can't be opened"),
}


async def early_replay(sessions: async_sessionmaker[AsyncSession], idem: Idempotency, now: datetime) -> None:
    """Answer a completed request from the idempotency store before redoing slow work. The write's
    own claim (house_write) stays the authority for concurrent duplicates."""
    async with sessions() as db:
        row = (
            await db.execute(
                text(
                    "SELECT request_hash, status_code, response_body, created_at FROM idempotency_keys "
                    "WHERE user_id = :u AND key = :k"
                ),
                {"u": idem.user_id, "k": idem.key},
            )
        ).first()
    if row is None or row.status_code is None or row.created_at < now - IDEMPOTENCY_TTL:
        return
    if row.request_hash != idem.request_hash:
        raise Problem(422, ErrorCode.IDEMPOTENCY_MISMATCH, "This Idempotency-Key was used for a different request")
    raise IdempotentReplay(int(row.status_code), row.response_body)


async def _draft_for_upload(sessions: async_sessionmaker[AsyncSession], bill_id: uuid.UUID, user_id: uuid.UUID) -> Bill:
    async with sessions() as db:
        bill = await db.get(Bill, bill_id)
        if bill is None or bill.deleted_at is not None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
        me = await membership(db, bill.house_id, user_id)
        require(Action.RECEIPT_UPLOAD, actor=me, bill=bill_ctx(bill))
    return bill


def _unavailable(exc: StorageError) -> Problem:
    log.warning("storage unavailable: %s", exc)
    return Problem(503, ErrorCode.INTERNAL, "Couldn't reach file storage", "Try again in a moment.")


# ---- upload ticket ----------------------------------------------------------------------------------


async def authorize(
    sessions: async_sessionmaker[AsyncSession],
    storage: Storage,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    mime: str,
    size: int,
    pages: int | None,
    storage_key: str | None,
) -> dict[str, Any]:
    check_file(mime, size, pages)
    await _draft_for_upload(sessions, bill_id, user_id)
    key = storage_key or new_key(bill_id, mime)
    check_key(key, bill_id, mime)
    try:
        ticket = await storage.upload_ticket(key, mime, size)
    except StorageError as exc:
        raise _unavailable(exc) from exc
    return ticket.wire()


# ---- extraction outcomes ------------------------------------------------------------------------------


@dataclass
class Outcome:
    kind: Kind
    extracted: ExtractedBill | None = None
    model: str = LOCAL_MODEL
    prompt_version: str = PARSER_VERSION
    latency_ms: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _elapsed_ms(started_ns: int) -> int:
    return (time.perf_counter_ns() - started_ns) // 1_000_000


def _from_parse(ex: ExtractedBill, started_ns: int) -> Outcome:
    if ex.status == "failed":
        return Outcome("failed", ex, latency_ms=_elapsed_ms(started_ns), error="not_a_receipt_or_unreadable")
    return Outcome("done", ex, latency_ms=_elapsed_ms(started_ns))


def parse_pdf(data: bytes) -> Outcome:
    """Text PDF → parsed bill; a scan → needs_ocr. PdfRejected propagates (the file is refused)."""
    started = time.perf_counter_ns()
    res = extract_pdf_lines(data)
    if res.needs_ocr:
        return Outcome("needs_ocr", warnings=res.warnings)
    try:
        ex = parse_receipt(res.lines, source="pdf_text", warnings=res.warnings)
    except Exception as exc:  # a parser bug must not lose the upload (EXTRACT-06)
        log.exception("receipt parser failed on a text pdf")
        return Outcome("failed", latency_ms=_elapsed_ms(started), error=f"parser_error: {type(exc).__name__}")
    return _from_parse(ex, started)


def parse_ocr(parts: Sequence[dict[str, Any]]) -> Outcome:
    """Browser OCR, one part per photo (merged across seams when there are several, EXTRACT-08)."""
    started = time.perf_counter_ns()
    try:
        line_parts = [lines_from_ocr(p) for p in parts]
        warnings: list[str] = []
        lines: list[Line]
        if len(line_parts) > 1:
            merged = merge_parts(line_parts)
            lines, warnings = merged.lines, merged.warnings
        else:
            lines = line_parts[0] if line_parts else []
        ex = parse_receipt(lines, source="ocr", warnings=warnings)
    except Exception as exc:
        log.exception("receipt parser failed on ocr lines")
        return Outcome("failed", latency_ms=_elapsed_ms(started), error=f"parser_error: {type(exc).__name__}")
    return _from_parse(ex, started)


async def plugin_slot(writes: WriteContext, house_id: uuid.UUID, cap: int) -> None:
    """The optional LLM plugin's per-house cap: `cap` extractions per day, 429 after (EXTRACT-09)."""
    key = hashlib.sha256(f"llm_extract:{house_id}".encode()).hexdigest()
    try:
        async with writes.sessions() as db, db.begin():
            await ratelimit.hit(db, writes.clock, "llm_extract", key, limit=cap, window=timedelta(days=1))
    except Problem as exc:
        raise Problem(
            429,
            ErrorCode.RATE_LIMITED,
            "That's all the automatic receipt reading for today",
            "Check the items by hand, or try again tomorrow.",
            extra=exc.extra,
            headers=exc.headers,
        ) from exc


async def run_plugin(plugin: ReceiptExtractor, data: bytes) -> Outcome:
    started = time.perf_counter_ns()
    try:
        raw = await asyncio.wait_for(plugin.extract([data]), PLUGIN_TIMEOUT_SECONDS)
        ex = ExtractedBill.model_validate({**raw, "source": "llm"}).recompute()
    except (TimeoutError, ExtractionFailed, ValidationError, ValueError, TypeError) as exc:
        return Outcome(
            "failed",
            model=plugin.model,
            prompt_version=plugin.prompt_version,
            latency_ms=_elapsed_ms(started),
            error=f"plugin_error: {type(exc).__name__}",
        )
    out = _from_parse(ex, started)
    out.model, out.prompt_version = plugin.model, plugin.prompt_version
    return out


# ---- applying to the draft -------------------------------------------------------------------------


def _confidence(x1000: int) -> Decimal:
    """Thousandths → numeric(3,2), rounded down so a flagged 0.795 never displays as 0.80."""
    return (Decimal(x1000) / Decimal(1000)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _fits(ex: ExtractedBill) -> str | None:
    if not 0 <= ex.computed_total_cents <= MAX_BILL_TOTAL_CENTS:
        return "total_out_of_range"
    if (ex.tax_cents or 0) < 0:
        return "negative_tax"
    return None


def _new_row(bill: Bill, outcome: Outcome, status: str, now: datetime) -> ReceiptExtraction:
    return ReceiptExtraction(
        id=uuid7(),
        bill_id=bill.id,
        model=outcome.model,
        prompt_version=outcome.prompt_version,
        raw_json=outcome.extracted.model_dump(mode="json") if outcome.extracted else None,
        latency_ms=outcome.latency_ms,
        cost_usd=Decimal(0) if outcome.model == LOCAL_MODEL else None,
        status=status,
        error=outcome.error[:500] if outcome.error else None,
        created_at=now,
    )


async def _supersede_offers(w: HouseWrite, bill_id: uuid.UUID, keep: uuid.UUID) -> None:
    """Only the newest scan can be offered; older offers are closed."""
    await w.session.execute(
        update(ReceiptExtraction)
        .where(
            ReceiptExtraction.bill_id == bill_id,
            ReceiptExtraction.status == OFFERED,
            ReceiptExtraction.id != keep,
        )
        .values(status="failed", error="superseded")
    )


async def hand_edited(db: AsyncSession, bill_id: uuid.UUID) -> bool:
    """Items typed by hand (no `raw_text`) or scanned items the payer edited (version > 1)."""
    found = await db.scalar(
        select(ItemRow.id)
        .where(
            ItemRow.bill_id == bill_id,
            ItemRow.deleted_at.is_(None),
            or_(ItemRow.raw_text.is_(None), ItemRow.version > 1),
        )
        .limit(1)
    )
    return found is not None


async def _apply(
    w: HouseWrite, bill: Bill, outcome: Outcome, now: datetime, row: ReceiptExtraction | None = None
) -> dict[str, Any]:
    """Replace the draft's items, fill header, tax, fees and printed total (one transaction). Callers
    make sure nothing typed by hand is lost: either there is none, or the payer chose the scan."""
    ex = outcome.extracted
    assert ex is not None
    problem = _fits(ex)
    if problem is not None:
        outcome.kind, outcome.error = "failed", problem
        return await _fail(w, bill, outcome, now, row)
    rows = await bill_math.load(w.session, bill)
    for old in rows.items:
        old.deleted_at = now
        old.version += 1
        old.updated_at = now
    warnings = list(ex.warnings)
    items = ex.items[:MAX_ITEMS_PER_BILL]
    for pos, it in enumerate(items):
        w.session.add(
            ItemRow(
                id=uuid7(),
                bill_id=bill.id,
                position=pos,
                raw_text=(it.raw_text or it.name)[:2000],
                name=(" ".join(it.name.split()) or "Item")[:120],
                quantity=it.quantity,
                unit_price_cents=it.unit_price_cents,
                discount_cents=it.discount_cents,
                line_total_cents=it.net_cents,  # items.line_total_cents is after line discounts
                taxable=it.taxable,
                confidence=_confidence(it.confidence_x1000),
                flagged=it.flagged,
                storage=storage_for(it.category, it.storage),
                category=it.category,
                icon_key=it.icon_key,
                shelf_life_days=it.shelf_life_days or None,
                created_at=now,
                updated_at=now,
            )
        )
    house = await w.session.get(House, bill.house_id)
    if not bill.store_name and ex.store_name:
        bill.store_name = ex.store_name[:80]
    if ex.purchase_date and bill.purchase_date == _today(house, bill.created_at):
        bill.purchase_date = ex.purchase_date  # FR-16: extracted date unless the payer picked one
    bill.tax_cents = ex.tax_cents or 0
    for fee in [f for f in rows.fees if f.kind != "adjustment"]:
        await w.session.delete(fee)
    for pos, f in enumerate(ex.fees):
        w.session.add(
            BillFee(
                id=uuid7(),
                bill_id=bill.id,
                kind=f.kind,  # tip stays its own kind and is allocated like a fee (§6)
                label=f.label[:40],
                amount_cents=f.amount_cents,
                position=pos,
                created_at=now,
            )
        )
    printed = ex.total_cents if ex.total_cents is not None and 0 <= ex.total_cents <= MAX_BILL_TOTAL_CENTS else None
    if printed != bill.receipt_total_cents:
        bill.printed_total_cents = None  # a new printed total clears an FR-13 override
        bill.reconcile_override_reason = None
    bill.receipt_total_cents = printed
    await w.session.flush()
    rows = await bill_math.load(w.session, bill)
    await _refresh_totals(w.session, rows, now)
    bill.extraction_status = "done"
    bill.version += 1
    if row is None:
        row = _new_row(bill, outcome, "done", now)
        w.session.add(row)
    else:
        row.status, row.error = "done", None
    await w.session.flush()
    await _supersede_offers(w, bill.id, row.id)
    recon = bill_math.reconciliation(rows)
    summary = {
        "extraction_id": str(row.id),
        "source": ex.source,
        "status": ex.status,
        "items": len(items),
        "flagged": sum(1 for i in items if i.flagged),
        "store_name": bill.store_name,
        "purchase_date": bill.purchase_date.isoformat(),
        "receipt_total_cents": bill.receipt_total_cents,
        "computed_cents": recon["computed_cents"],
        "difference_cents": recon["difference_cents"],
        "warnings": warnings[:20],
    }
    w.emit(
        "extraction_applied",
        aggregate=("bill", bill.id, bill.version),
        data={"bill_id": str(bill.id), **summary},
        activity=Activity(
            "receipt_extracted",
            bill_id=bill.id,
            payload={k: summary[k] for k in ("source", "items", "flagged", "difference_cents")},
        ),
    )
    return summary


def offer_view(row: ReceiptExtraction) -> dict[str, Any] | None:
    """The "Use the scanned items instead?" banner (PRD §21.6, SYNC-15)."""
    if row.raw_json is None:
        return None
    ex = ExtractedBill.model_validate(row.raw_json)
    return {
        "extraction_id": str(row.id),
        "item_count": len(ex.items),
        "total_cents": ex.total_cents,
        "computed_cents": ex.computed_total_cents,
        "store_name": ex.store_name,
        "source": ex.source,
        "created_at": row.created_at.isoformat(),
    }


async def _offer(w: HouseWrite, bill: Bill, outcome: Outcome, now: datetime) -> dict[str, Any]:
    """The payer already typed or edited items: keep them, store the scan and offer it."""
    ex = outcome.extracted
    assert ex is not None
    problem = _fits(ex)
    if problem is not None:
        outcome.kind, outcome.error = "failed", problem
        return await _fail(w, bill, outcome, now)
    outcome.kind = "offered"
    row = _new_row(bill, outcome, OFFERED, now)
    w.session.add(row)
    await w.session.flush()
    await _supersede_offers(w, bill.id, row.id)
    bill.extraction_status = "done"
    bill.version += 1
    bill.updated_at = now
    summary = offer_view(row)
    assert summary is not None
    w.emit(
        "extraction_offered",
        aggregate=("bill", bill.id, bill.version),
        data={"bill_id": str(bill.id), **summary},
        activity=Activity("receipt_scan_offered", bill_id=bill.id, payload={"items": summary["item_count"]}),
    )
    return summary


async def _fail(
    w: HouseWrite, bill: Bill, outcome: Outcome, now: datetime, row: ReceiptExtraction | None = None
) -> dict[str, Any]:
    """EXTRACT-06: status failed, nothing invented, the file stays attached; retry or enter by hand."""
    bill.extraction_status = "failed"
    bill.version += 1
    bill.updated_at = now
    if row is None:
        row = _new_row(bill, outcome, "failed", now)
        w.session.add(row)
    else:
        row.status, row.error = "failed", (outcome.error or "failed")[:500]
    summary = {"extraction_id": str(row.id), "error": outcome.error}
    w.emit(
        "extraction_failed",
        aggregate=("bill", bill.id, bill.version),
        data={"bill_id": str(bill.id), **summary},
        activity=Activity("receipt_extraction_failed", bill_id=bill.id, payload={"error": outcome.error}),
    )
    return summary


async def _settle_outcome(w: HouseWrite, bill: Bill, outcome: Outcome, now: datetime) -> dict[str, Any] | None:
    if outcome.kind == "done":
        # PRD §21.6 / SYNC-15: scanned results never overwrite items the payer typed or edited.
        if await hand_edited(w.session, bill.id):
            return await _offer(w, bill, outcome, now)
        return await _apply(w, bill, outcome, now)
    if outcome.kind == "failed":
        return await _fail(w, bill, outcome, now)
    bill.extraction_status = "pending"  # waiting for the browser's OCR
    bill.version += 1
    bill.updated_at = now
    return None


async def scanned_offer(db: AsyncSession, bill_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.scalars(
            select(ReceiptExtraction)
            .where(ReceiptExtraction.bill_id == bill_id, ReceiptExtraction.status == OFFERED)
            .order_by(ReceiptExtraction.created_at.desc())
            .limit(1)
        )
    ).first()
    return offer_view(row) if row is not None else None


async def decide_offer(
    writes: WriteContext,
    bill_id: uuid.UUID,
    extraction_id: uuid.UUID,
    user_id: uuid.UUID,
    accept: bool,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """Accept ("Use the scanned items instead": replaces the items) or dismiss a stored scan.
    A transition (PRD §21.3): Idempotency-Key, bill row locked, only an offered scan can move."""
    bill = await _draft_for_upload(writes.sessions, bill_id, user_id)
    async with house_write(writes, bill.house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        locked = await _lock_bill(w, bill_id)
        require(Action.RECEIPT_UPLOAD, actor=me, bill=bill_ctx(locked))
        row = (
            await w.session.scalars(
                select(ReceiptExtraction)
                .where(ReceiptExtraction.id == extraction_id, ReceiptExtraction.bill_id == bill_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "That scan isn't on this tab")
        if row.status != OFFERED or row.raw_json is None:
            raise Problem(409, ErrorCode.CONFLICT, "That scan was already used or dismissed")
        if accept:
            outcome = Outcome(
                "done",
                ExtractedBill.model_validate(row.raw_json),
                model=row.model,
                prompt_version=row.prompt_version,
                latency_ms=row.latency_ms or 0,
            )
            result = await _apply(w, locked, outcome, now, row)
            out: dict[str, Any] = {"extraction": outcome.kind, "result": result, "bill": _bill_brief(locked)}
        else:
            row.status, row.error = "failed", "dismissed"
            locked.version += 1
            locked.updated_at = now
            w.emit(
                "extraction_dismissed",
                aggregate=("bill", locked.id, locked.version),
                data={"bill_id": str(locked.id), "extraction_id": str(row.id)},
                activity=Activity("receipt_scan_dismissed", bill_id=locked.id),
            )
            out = {"extraction": "dismissed", "result": None, "bill": _bill_brief(locked)}
        w.respond(200, out)
    return out


def receipt_view(r: Receipt) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "bill_id": str(r.bill_id),
        "mime": r.mime,
        "bytes": r.bytes,
        "page_index": r.page_index,
        "created_at": r.created_at.isoformat(),
        "deleted": r.deleted_at is not None,
    }


def _bill_brief(bill: Bill) -> dict[str, Any]:
    return {
        "id": str(bill.id),
        "version": bill.version,
        "extraction_status": bill.extraction_status,
        "total_cents": bill.total_cents,
        "receipt_total_cents": bill.receipt_total_cents,
    }


# ---- attach a stored file ------------------------------------------------------------------------------


async def attach(
    writes: WriteContext,
    storage: Storage,
    plugin: ReceiptExtractor | None,
    plugin_cap: int,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    key: str,
    mime: str,
    size: int,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    check_file(mime, size)
    await early_replay(writes.sessions, idem, now)
    bill = await _draft_for_upload(writes.sessions, bill_id, user_id)
    check_key(key, bill_id, mime)
    outcome = await _read_and_extract(writes, storage, plugin, plugin_cap, bill.house_id, key, mime, size)

    async with house_write(writes, bill.house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        locked = await _lock_bill(w, bill_id)
        require(Action.RECEIPT_UPLOAD, actor=me, bill=bill_ctx(locked))
        if await w.session.scalar(select(Receipt.id).where(Receipt.storage_key == key)) is not None:
            raise Problem(409, ErrorCode.CONFLICT, "That file is already attached")
        page_index = int(
            await w.session.scalar(select(func.count()).select_from(Receipt).where(Receipt.bill_id == bill_id)) or 0
        )
        receipt = Receipt(
            id=uuid7(),
            bill_id=bill_id,
            storage_key=key,
            mime=mime,
            bytes=size,
            page_index=page_index,
            uploaded_by=user_id,
            created_at=now,
        )
        w.session.add(receipt)
        await w.session.flush()
        w.emit(
            "receipt_added",
            aggregate=("bill", locked.id, locked.version + 1),
            data={"bill_id": str(bill_id), "receipt_id": str(receipt.id), "extraction": outcome.kind},
            activity=Activity("receipt_added", bill_id=bill_id, actor_user_id=user_id, payload={"mime": mime}),
        )
        summary = await _settle_outcome(w, locked, outcome, now)
        out = {
            "receipt": receipt_view(receipt),
            "extraction": outcome.kind,
            "result": summary,
            "bill": _bill_brief(locked),
        }
        w.respond(201, out)
    return out


async def _read_and_extract(
    writes: WriteContext,
    storage: Storage,
    plugin: ReceiptExtractor | None,
    plugin_cap: int,
    house_id: uuid.UUID,
    key: str,
    mime: str,
    size: int,
) -> Outcome:
    """Verify the stored object (exists, size, magic bytes) and extract what can be extracted here.
    A refused file is deleted from storage so nothing unusable lingers."""
    try:
        info = await storage.head(key)
        if info is None:
            raise Problem(409, ErrorCode.CONFLICT, "We couldn't find the uploaded file", "Upload it again.")
        if info.size > MAX_BYTES:
            await storage.delete(key)
            raise Problem(413, ErrorCode.FILE_TOO_LARGE, "That file is over 20 MB")
        if info.size != size:
            await storage.delete(key)
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "The upload didn't finish", "Upload it again.")
        if sniff(await storage.get(key, first_bytes=SNIFF_BYTES)) != mime:
            await storage.delete(key)
            raise Problem(415, ErrorCode.UNSUPPORTED_MEDIA, "That file isn't the photo or PDF it claims to be")
        data: bytes | None = None
        outcome = Outcome("needs_ocr")
        if mime == PDF:
            data = await storage.get(key)
            try:
                outcome = await asyncio.to_thread(parse_pdf, data)
            except PdfRejected as exc:
                await storage.delete(key)
                status, code, title = PDF_PROBLEMS[exc.reason]
                raise Problem(status, code, title) from exc
        if outcome.kind == "needs_ocr" and plugin is not None:
            await plugin_slot(writes, house_id, plugin_cap)
            if data is None:
                data = await storage.get(key)
            outcome = await run_plugin(plugin, data)
        return outcome
    except StorageError as exc:
        raise _unavailable(exc) from exc


# ---- browser OCR -------------------------------------------------------------------------------------


async def apply_ocr(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    receipt_id: uuid.UUID | None,
    parts: list[dict[str, Any]],
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    await early_replay(writes.sessions, idem, now)
    bill = await _draft_for_upload(writes.sessions, bill_id, user_id)
    outcome = await asyncio.to_thread(parse_ocr, parts)
    async with house_write(writes, bill.house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        locked = await _lock_bill(w, bill_id)
        require(Action.RECEIPT_UPLOAD, actor=me, bill=bill_ctx(locked))
        if receipt_id is not None:
            found = await w.session.scalar(
                select(Receipt.id).where(
                    Receipt.id == receipt_id, Receipt.bill_id == bill_id, Receipt.deleted_at.is_(None)
                )
            )
            if found is None:
                raise Problem(404, ErrorCode.NOT_FOUND, "That receipt isn't on this tab")
        summary = await _settle_outcome(w, locked, outcome, now)
        out = {"extraction": outcome.kind, "result": summary, "bill": _bill_brief(locked)}
        w.respond(200, out)
    return out


# ---- reading -------------------------------------------------------------------------------------------


async def list_for_bill(
    sessions: async_sessionmaker[AsyncSession], bill_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    async with sessions() as db:
        bill = await db.get(Bill, bill_id)
        if bill is None or bill.deleted_at is not None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
        me = await membership(db, bill.house_id, user_id)
        require(Action.RECEIPT_VIEW, actor=me)
        rows = (await db.scalars(select(Receipt).where(Receipt.bill_id == bill_id).order_by(Receipt.page_index))).all()
    return [receipt_view(r) for r in rows]


async def view(
    sessions: async_sessionmaker[AsyncSession],
    storage: Storage,
    receipt_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> dict[str, Any]:
    """FR-18 / EXTRACT-10: a short-lived URL, for signed-in members of the bill's house only."""
    async with sessions() as db:
        receipt = await db.get(Receipt, receipt_id)
        bill = await db.get(Bill, receipt.bill_id) if receipt is not None else None
        if receipt is None or bill is None or bill.deleted_at is not None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Receipt not found")
        me = await membership(db, bill.house_id, user_id)
        require(Action.RECEIPT_VIEW, actor=me)
    if receipt.deleted_at is not None:
        raise Problem(410, ErrorCode.GONE, "This receipt file was deleted", "Receipt files are kept 2 years.")
    try:
        url = await storage.view_url(receipt.storage_key, VIEW_TTL)
    except StorageError as exc:
        raise _unavailable(exc) from exc
    return {"url": url, "mime": receipt.mime, "expires_at": (now + VIEW_TTL).isoformat()}
