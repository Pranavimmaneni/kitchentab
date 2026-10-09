"""Open-bill life endpoints: payments, settle, reopen, void, reminders, activity, seen (FR-35..FR-43)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select

from app.deps import Actor, client_mutation_id, ctx, idempotency, require_actor
from app.errors import ErrorCode, Problem
from app.models import ActivityEvent, Bill, Member
from app.policy import Action, require
from app.services import bill_life as svc
from app.services.bills import bill_ctx
from app.services.houses import membership

router = APIRouter(tags=["open bills"])


class NewPayment(BaseModel):
    amount_cents: Annotated[int, Field(gt=0, le=10_000_000)] | None = None
    method: Literal["venmo", "cashapp", "paypal", "zelle", "cash", "other"] = "other"
    note: str | None = Field(default=None, max_length=280)
    participant: str | None = Field(default=None, pattern=r"^[mg]:[0-9a-f-]{36}$")  # payer recording for someone


@router.post("/bills/{bill_id}/payments", status_code=201)
async def record_payment(
    bill_id: uuid.UUID, body: NewPayment, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.record_payment(
        c.writes,
        bill_id,
        actor.user.id,
        body.model_dump(),
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


class Reject(BaseModel):
    note: str | None = Field(default=None, max_length=280)


@router.post("/payments/{payment_id}/confirm")
async def confirm(payment_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.answer_payment(
        c.writes,
        payment_id,
        actor.user.id,
        True,
        None,
        c.clock.now(),
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


@router.post("/payments/{payment_id}/reject")
async def reject(
    payment_id: uuid.UUID, body: Reject, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.answer_payment(
        c.writes,
        payment_id,
        actor.user.id,
        False,
        body.note,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


class Settle(BaseModel):
    confirm_outstanding: bool = False


@router.post("/bills/{bill_id}/settle")
async def settle(
    bill_id: uuid.UUID, body: Settle, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.settle(
        c.writes,
        bill_id,
        actor.user.id,
        body.confirm_outstanding,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


@router.post("/bills/{bill_id}/reopen")
async def reopen(bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.reopen(
        c.writes, bill_id, actor.user.id, c.clock.now(), idempotency(request, actor, None), client_mutation_id(request)
    )


class Void(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/bills/{bill_id}/void")
async def void(
    bill_id: uuid.UUID, body: Void, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.void(
        c.writes,
        bill_id,
        actor.user.id,
        body.reason,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


class Remind(BaseModel):
    member_id: uuid.UUID


@router.post("/bills/{bill_id}/remind")
async def remind(
    bill_id: uuid.UUID, body: Remind, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.remind(
        c.writes,
        bill_id,
        actor.user.id,
        body.member_id,
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


async def _readable_bill(request: Request, bill_id: uuid.UUID, actor: Actor) -> tuple[Bill, Any]:
    async with ctx(request).sessions() as db:
        bill = await db.get(Bill, bill_id)
        if bill is None or bill.deleted_at is not None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
        me = await membership(db, bill.house_id, actor.user.id)
        require(Action.BILL_READ, actor=me, bill=bill_ctx(bill))
    return bill, me


@router.get("/bills/{bill_id}/money")
async def money(
    bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    """Who owes what on an open bill: payments, owed per person, and my change badge (FR-35)."""
    from app.services import bill_math

    bill, me = await _readable_bill(request, bill_id, actor)
    async with ctx(request).sessions() as db:
        payments = await svc.payments_for(db, bill_id)
        rows = await bill_math.load(db, bill)
        owed = svc.owed_map(rows, payments) if bill.state != "draft" and not rows.unassigned else {}
        changed = await svc.changed_since_seen(db, actor.user.id, me.member_id, bill)
    return {
        "payments": [svc.payment_view(p) for p in payments],
        "owed": owed,
        "changed": changed,
        "version": bill.version,
    }


class Seen(BaseModel):
    version: int = Field(ge=1)


@router.put("/bills/{bill_id}/seen", status_code=204)
async def seen(
    bill_id: uuid.UUID, body: Seen, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> None:
    """Set-state: clears the "changed" badge up to `version` (FR-35)."""
    await _readable_bill(request, bill_id, actor)
    c = ctx(request)
    async with c.sessions() as db, db.begin():
        await svc.mark_seen(db, actor.user.id, bill_id, body.version, c.clock.now())


@router.get("/bills/{bill_id}/activity")
async def activity(
    bill_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    cursor: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """FR-43: newest first, cursor pagination (`created_at|id`), max 50."""
    await _readable_bill(request, bill_id, actor)
    limit = max(1, min(limit, 50))
    q = (
        select(ActivityEvent, Member.display_name)
        .join(Member, Member.id == ActivityEvent.actor_member_id, isouter=True)
        .where(ActivityEvent.bill_id == bill_id)
    )
    if cursor:
        at_raw, _, id_raw = cursor.partition("|")
        at, cid = datetime.fromisoformat(at_raw), uuid.UUID(id_raw)
        q = q.where(or_(ActivityEvent.created_at < at, and_(ActivityEvent.created_at == at, ActivityEvent.id < cid)))
    async with ctx(request).sessions() as db:
        rows = (
            await db.execute(q.order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc()).limit(limit + 1))
        ).all()
    page = rows[:limit]
    next_cursor = f"{page[-1][0].created_at.isoformat()}|{page[-1][0].id}" if len(rows) > limit else None
    return {
        "events": [
            {
                "id": str(e.id),
                "type": e.type,
                "actor_name": name,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e, name in page
        ],
        "next_cursor": next_cursor,
    }
