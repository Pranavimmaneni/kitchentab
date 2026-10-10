"""Receipt upload, attach, browser OCR and private viewing (PRD §9, §11; FR-10..FR-12, FR-18;
ADR-0010, ADR-0015). Authorization is decided in `app.policy`; the work is in `services.receipts`."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.extractor import ReceiptExtractor, build_plugin
from app.adapters.storage import Storage, StorageError, storage_from_settings
from app.deps import Actor, client_mutation_id, ctx, idempotency, require_actor
from app.errors import ErrorCode, Problem
from app.services import receipts as svc

router = APIRouter(tags=["receipts"])
NO_STORE = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}


def get_storage(request: Request) -> Storage:
    """One storage adapter per app, from settings (tests may set `app.state.storage`)."""
    state = request.app.state
    storage: Storage | None = getattr(state, "storage", None)
    if storage is None:
        try:
            storage = storage_from_settings(state.settings)
        except StorageError as exc:
            raise Problem(503, ErrorCode.INTERNAL, "File storage isn't configured") from exc
        state.storage = storage
    return storage


def get_plugin(request: Request) -> ReceiptExtractor | None:
    """The optional LLM plugin: only with EXTRACTOR_API_KEY set (EXTRACT-15)."""
    state = request.app.state
    if not hasattr(state, "extractor"):
        state.extractor = build_plugin(state.settings, getattr(state, "redis", None))
    plugin: ReceiptExtractor | None = state.extractor
    return plugin


class Authorize(BaseModel):
    bill_id: uuid.UUID
    mime: str = Field(min_length=3, max_length=100)
    bytes: int = Field(gt=0)
    pages: int | None = Field(default=None, gt=0)
    # Vercel Blob client uploads choose the pathname in the browser; the Next.js route forwards it
    # here so the policy check covers exactly that key (ADR-0015).
    storage_key: str | None = Field(default=None, max_length=200)


@router.post("/uploads/authorize")
async def authorize(
    body: Authorize,
    request: Request,
    response: Response,
    actor: Annotated[Actor, Depends(require_actor)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> dict[str, Any]:
    """An upload ticket for one receipt file; nothing is written (no Idempotency-Key needed)."""
    response.headers.update(NO_STORE)
    return await svc.authorize(
        ctx(request).sessions,
        storage,
        body.bill_id,
        actor.user.id,
        body.mime.lower(),
        body.bytes,
        body.pages,
        body.storage_key,
    )


class Attach(BaseModel):
    storage_key: str = Field(min_length=1, max_length=200)
    mime: str = Field(min_length=3, max_length=100)
    bytes: int = Field(gt=0)


@router.post("/bills/{bill_id}/receipts", status_code=201)
async def attach(
    bill_id: uuid.UUID,
    body: Attach,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    storage: Annotated[Storage, Depends(get_storage)],
    plugin: Annotated[ReceiptExtractor | None, Depends(get_plugin)],
) -> Any:
    c = ctx(request)
    return await svc.attach(
        c.writes,
        storage,
        plugin,
        c.settings.extractor_daily_cap_per_house,
        bill_id,
        actor.user.id,
        body.storage_key,
        body.mime.lower(),
        body.bytes,
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


@router.get("/bills/{bill_id}/receipts")
async def list_receipts(
    bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> list[dict[str, Any]]:
    return await svc.list_for_bill(ctx(request).sessions, bill_id, actor.user.id)


# Browser OCR payload (tesseract.js lines with boxes and confidences; app.extraction.lines).
class OcrWord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(max_length=200)
    bbox: list[Decimal] = Field(min_length=4, max_length=4)
    confidence: Decimal = Field(default=Decimal(100), ge=0, le=100)


class OcrLine(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(max_length=500)
    bbox: list[Decimal] = Field(min_length=4, max_length=4)
    confidence: Decimal = Field(default=Decimal(100), ge=0, le=100)
    words: list[OcrWord] = Field(default_factory=list, max_length=200)


class OcrPage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page: int | None = Field(default=None, ge=1, le=100)
    width: Decimal | None = None
    height: Decimal | None = None
    lines: list[OcrLine] = Field(default_factory=list, max_length=3000)


class OcrPart(BaseModel):
    pages: list[OcrPage] = Field(min_length=1, max_length=10)


class Ocr(BaseModel):
    receipt_id: uuid.UUID | None = None
    parts: list[OcrPart] = Field(min_length=1, max_length=10)  # one part per photo


@router.post("/bills/{bill_id}/ocr")
async def ocr(bill_id: uuid.UUID, body: Ocr, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.apply_ocr(
        c.writes,
        bill_id,
        actor.user.id,
        body.receipt_id,
        [p.model_dump(exclude_none=True) for p in body.parts],
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


@router.get("/receipts/{receipt_id}/view")
async def view(
    receipt_id: uuid.UUID,
    request: Request,
    response: Response,
    actor: Annotated[Actor, Depends(require_actor)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> dict[str, Any]:
    response.headers.update(NO_STORE)
    c = ctx(request)
    return await svc.view(c.sessions, storage, receipt_id, actor.user.id, c.clock.now())


@router.post("/bills/{bill_id}/extractions/{extraction_id}/apply")
async def apply_offer(
    bill_id: uuid.UUID, extraction_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    """ "Use the scanned items instead": the stored scan replaces the draft's items (SYNC-15)."""
    c = ctx(request)
    return await svc.decide_offer(
        c.writes,
        bill_id,
        extraction_id,
        actor.user.id,
        True,
        c.clock.now(),
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


@router.post("/bills/{bill_id}/extractions/{extraction_id}/dismiss")
async def dismiss_offer(
    bill_id: uuid.UUID, extraction_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    """Keep the items typed by hand; the stored scan is no longer offered."""
    c = ctx(request)
    return await svc.decide_offer(
        c.writes,
        bill_id,
        extraction_id,
        actor.user.id,
        False,
        c.clock.now(),
        idempotency(request, actor, None),
        client_mutation_id(request),
    )
