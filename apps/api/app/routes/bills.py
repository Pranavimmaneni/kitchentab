"""Bills, items, allocations, send and the public bill link (PRD §11; FR-13..FR-31)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import (
    Actor,
    client_ip,
    client_mutation_id,
    ctx,
    idempotency,
    if_match,
    optional_actor,
    rate_limit,
    require_actor,
)
from app.errors import ErrorCode, Problem
from app.links import looks_like_token
from app.models import Bill, House, Member
from app.policy import Action, require
from app.services import bills as svc
from app.services.houses import membership
from app.tokens import hash_token

router = APIRouter(tags=["bills"])
LINK_HEADERS = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex", "Cache-Control": "private, no-store"}
Cents = Annotated[int, Field(ge=-10_000_000, le=10_000_000)]


def _pepper(request: Request) -> str:
    return ctx(request).settings.token_hash_pepper.get_secret_value()


class NewBill(BaseModel):
    store_name: str | None = Field(default=None, max_length=80)
    purchase_date: date | None = None
    payer_member_id: uuid.UUID | None = None
    receipt_total_cents: Annotated[int, Field(ge=0, le=10_000_000)] | None = None


@router.post("/houses/{house_id}/bills", status_code=201)
async def create_bill(
    house_id: uuid.UUID, body: NewBill, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    data = body.model_dump(mode="json")
    if body.purchase_date:
        data["purchase_date"] = body.purchase_date
    return await svc.create_bill(
        c.writes,
        house_id,
        actor.user.id,
        data,
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


@router.get("/houses/{house_id}/bills")
async def list_bills(
    house_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    state: Literal["draft", "open", "settled", "voided"] | None = None,
) -> list[dict[str, Any]]:
    async with ctx(request).sessions() as db:
        me = await membership(db, house_id, actor.user.id)
        require(Action.HOUSE_READ, actor=me)
        q = select(Bill).where(Bill.house_id == house_id, Bill.deleted_at.is_(None))
        if state:
            q = q.where(Bill.state == state)
        bills = (await db.scalars(q.order_by(Bill.purchase_date.desc(), Bill.created_at.desc()).limit(50))).all()
    return [
        {
            "id": str(b.id),
            "store_name": b.store_name,
            "purchase_date": b.purchase_date.isoformat(),
            "state": b.state,
            "total_cents": b.total_cents,
            "payer_member_id": str(b.payer_member_id),
            "version": b.version,
        }
        for b in bills
    ]


@router.get("/bills/{bill_id}")
async def get_bill(
    bill_id: uuid.UUID, request: Request, response: Response, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    async with ctx(request).sessions() as db:
        bill = await db.get(Bill, bill_id)
        if bill is None or bill.deleted_at is not None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
        me = await membership(db, bill.house_id, actor.user.id)
        require(Action.BILL_READ, actor=me, bill=svc.bill_ctx(bill))
        view = await svc.bill_view(db, _pepper(request), bill, me)
    response.headers["ETag"] = f'"{bill.version}"'
    return view


class Fee(BaseModel):
    kind: Literal["fee", "tip", "order_discount"]
    label: str = Field(min_length=1, max_length=40)
    amount_cents: Annotated[int, Field(ge=0, le=10_000_000)]


class Header(BaseModel):
    store_name: str | None = Field(default=None, max_length=80)
    purchase_date: date | None = None
    payer_member_id: uuid.UUID | None = None
    tax_cents: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    receipt_total_cents: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    fees: list[Fee] | None = Field(default=None, max_length=20)


@router.patch("/bills/{bill_id}")
async def update_bill(
    bill_id: uuid.UUID, body: Header, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    c = ctx(request)
    data = body.model_dump(exclude_unset=True, mode="json")
    if body.purchase_date:
        data["purchase_date"] = body.purchase_date
    return await svc.update_header(
        c.writes, bill_id, actor.user.id, data, if_match(request), c.clock.now(), client_mutation_id(request)
    )


@router.delete("/bills/{bill_id}")
async def delete_bill(bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.delete_draft(
        c.writes, bill_id, actor.user.id, c.clock.now(), idempotency(request, actor, None), client_mutation_id(request)
    )


class Override(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/bills/{bill_id}/reconcile-override")
async def override(
    bill_id: uuid.UUID, body: Override, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.override_reconciliation(
        c.writes,
        bill_id,
        actor.user.id,
        body.reason,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


class NewItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    line_total_cents: Cents
    quantity: Annotated[Decimal, Field(gt=0, le=10_000, max_digits=10, decimal_places=3)] | None = None
    unit_price_cents: Cents | None = None
    discount_cents: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    taxable: bool | None = None
    storage: Literal["fridge", "freezer", "pantry", "none"] | None = None
    category: str | None = Field(default=None, max_length=20)
    icon_key: str | None = Field(default=None, max_length=30)
    shelf_life_days: Annotated[int, Field(gt=0, le=3650)] | None = None
    raw_text: str | None = Field(default=None, max_length=200)


def _item_body(body: BaseModel) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True)


@router.post("/bills/{bill_id}/items", status_code=201)
async def add_item(
    bill_id: uuid.UUID, body: NewItem, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.add_item(
        c.writes,
        bill_id,
        actor.user.id,
        _item_body(body),
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


class ItemPatch(NewItem):
    name: str | None = Field(default=None, min_length=1, max_length=120)  # type: ignore[assignment]
    line_total_cents: Cents | None = None  # type: ignore[assignment]


@router.patch("/bills/{bill_id}/items/{item_id}")
async def update_item(
    bill_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ItemPatch,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
) -> Any:
    c = ctx(request)
    return await svc.update_item(
        c.writes,
        bill_id,
        item_id,
        actor.user.id,
        _item_body(body),
        if_match(request),
        c.clock.now(),
        client_mutation_id(request),
    )


@router.delete("/bills/{bill_id}/items/{item_id}")
async def delete_item(
    bill_id: uuid.UUID, item_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.delete_item(c.writes, bill_id, item_id, actor.user.id, c.clock.now(), client_mutation_id(request))


class Order(BaseModel):
    item_ids: list[uuid.UUID] = Field(max_length=200)


@router.put("/bills/{bill_id}/items/order")
async def reorder(
    bill_id: uuid.UUID, body: Order, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    return await svc.reorder_items(
        ctx(request).writes, bill_id, actor.user.id, body.item_ids, client_mutation_id(request)
    )


class Merge(BaseModel):
    item_ids: list[uuid.UUID] = Field(min_length=2, max_length=200)
    name: str | None = Field(default=None, max_length=120)


@router.post("/bills/{bill_id}/items/merge", status_code=201)
async def merge(
    bill_id: uuid.UUID, body: Merge, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.merge_items(
        c.writes,
        bill_id,
        actor.user.id,
        body.item_ids,
        body.name,
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


class Part(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    line_total_cents: Cents


class Split(BaseModel):
    parts: list[Part] = Field(min_length=2, max_length=20)


@router.post("/bills/{bill_id}/items/{item_id}/split", status_code=201)
async def split(
    bill_id: uuid.UUID,
    item_id: uuid.UUID,
    body: Split,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
) -> Any:
    c = ctx(request)
    return await svc.split_item(
        c.writes,
        bill_id,
        item_id,
        actor.user.id,
        [p.model_dump() for p in body.parts],
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


class Cell(BaseModel):
    item_id: uuid.UUID
    participant: str = Field(pattern=r"^[mg]:[0-9a-f-]{36}$")
    on: bool


class Cells(BaseModel):
    changes: list[Cell] = Field(min_length=1, max_length=400)


@router.put("/bills/{bill_id}/allocations")
async def set_allocations(
    bill_id: uuid.UUID, body: Cells, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    """Set-state (PRD §21.3): send the desired value per cell, never a toggle; no If-Match."""
    c = ctx(request)
    return await svc.set_allocations(
        c.writes,
        bill_id,
        actor.user.id,
        [x.model_dump(mode="json") for x in body.changes],
        c.clock.now(),
        client_mutation_id(request),
    )


@router.post("/bills/{bill_id}/allocations/fill-rest")
async def fill_rest(bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.fill_rest(
        c.writes, bill_id, actor.user.id, c.clock.now(), idempotency(request, actor, None), client_mutation_id(request)
    )


class Exclusion(BaseModel):
    excluded: bool


@router.put("/bills/{bill_id}/exclusions/{member_id}")
async def set_exclusion(
    bill_id: uuid.UUID,
    member_id: uuid.UUID,
    body: Exclusion,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
) -> Any:
    c = ctx(request)
    return await svc.set_exclusion(
        c.writes, bill_id, actor.user.id, member_id, body.excluded, c.clock.now(), client_mutation_id(request)
    )


class NewGuest(BaseModel):
    name: str = Field(min_length=1, max_length=24)


@router.post("/bills/{bill_id}/guests", status_code=201)
async def add_guest(
    bill_id: uuid.UUID, body: NewGuest, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.add_guest(
        c.writes,
        _pepper(request),
        bill_id,
        actor.user.id,
        body.name,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


@router.get("/bills/{bill_id}/preview-shares")
async def preview_shares(
    bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    view = await get_bill(bill_id, request, Response(), actor)
    return {"shares": view["shares"], "final": view["shares_final"], "progress": view["progress"]}


@router.post("/bills/{bill_id}/send")
async def send(bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.send(
        c.writes,
        _pepper(request),
        bill_id,
        actor.user.id,
        c.clock.now(),
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


# ---- public bill link (FR-31, LINK-03) ---------------------------------------------------------------


@router.get("/b/{token}")
async def bill_link(
    token: str, request: Request, response: Response, actor: Annotated[Actor | None, Depends(optional_actor)]
) -> dict[str, Any]:
    """Read-only view of a sent bill for anyone holding the link; editing needs sign-in + a claim."""
    from app.routes.houses import _lockout_check
    from app.services import houses as house_svc

    await _lockout_check(request)
    c = ctx(request)
    bill = None
    if looks_like_token(token):
        async with c.sessions() as db:
            bill = (
                await db.scalars(
                    select(Bill).where(
                        Bill.link_token_hash == hash_token(token, _pepper(request)), Bill.deleted_at.is_(None)
                    )
                )
            ).first()
    if bill is None or bill.state == "voided":
        await rate_limit(request, "bad_link", client_ip(request), limit=20, window=timedelta(hours=1))
        raise Problem(
            410,
            ErrorCode.LINK_EXPIRED,
            "This link doesn't work anymore. Ask your house for a new one.",
            headers=LINK_HEADERS,
        )
    async with c.sessions() as db:
        house = await db.get(House, bill.house_id)
        assert house is not None
        me = await membership(db, bill.house_id, actor.user.id) if actor else None
        view = await svc.bill_view(db, _pepper(request), bill, me if me and me.active else None)
        payer = await db.get(Member, bill.payer_member_id)
        join = await house_svc.join_view(db, house, actor.user if actor else None)
    view["bill"].pop("link_token", None)
    view["house"] = {"name": house.name}
    view["payer_name"] = payer.display_name if payer else None
    view["join"] = {
        "placeholders": join["placeholders"],
        "my_member": join["my_member"],
        "signed_in": actor is not None,
        "invite_token": None,
    }
    response.headers.update(LINK_HEADERS)
    return view


class LinkClaim(BaseModel):
    member_id: uuid.UUID | None = None
    new_name: str | None = Field(default=None, min_length=1, max_length=24)


@router.post("/b/{token}/claim")
async def bill_link_claim(
    token: str, body: LinkClaim, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    """ "Which one are you?" from a bill link (PRD §4.3): same rules as the invite claim (FR-05)."""
    from app.routes.houses import _lockout_check
    from app.services import houses as house_svc

    await _lockout_check(request)
    c = ctx(request)
    bill = None
    if looks_like_token(token):
        async with c.sessions() as db:
            bill = (
                await db.scalars(
                    select(Bill).where(
                        Bill.link_token_hash == hash_token(token, _pepper(request)), Bill.deleted_at.is_(None)
                    )
                )
            ).first()
    if bill is None or bill.state == "voided":
        await rate_limit(request, "bad_link", client_ip(request), limit=20, window=timedelta(hours=1))
        raise Problem(410, ErrorCode.LINK_EXPIRED, "This link doesn't work anymore. Ask your house for a new one.")
    return await house_svc.claim(
        c.writes,
        bill.house_id,
        actor.user,
        body.member_id,
        body.new_name,
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )
