"""Tabs, balances, history, search and CSV export (PRD §5.5; FR-50..FR-54)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select

from app.deps import Actor, ctx, require_actor
from app.errors import ErrorCode, Problem
from app.models import Bill, Member
from app.models import Item as ItemRow
from app.policy import Action, require
from app.services import bill_math
from app.services import bills as bill_svc
from app.services.balances import balance_view
from app.services.bill_life import changed_since_seen, owed_map, payments_for
from app.services.houses import membership

router = APIRouter(tags=["tabs"])
PAGE = 20


async def _member_or_403(request: Request, house_id: uuid.UUID, actor: Actor) -> Any:
    async with ctx(request).sessions() as db:
        me = await membership(db, house_id, actor.user.id)
    require(Action.HOUSE_READ, actor=me)
    return me


@router.get("/houses/{house_id}/balances")
async def balances(
    house_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    me = await _member_or_403(request, house_id, actor)
    async with ctx(request).sessions() as db:
        return await balance_view(db, house_id, me.member_id)


async def _card(db: Any, bill: Bill, me: Any, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    """One tab card (FR-52): store, date, payer, total, payment progress, my status, change badge."""
    item_count = await db.scalar(
        select(func.count()).select_from(ItemRow).where(ItemRow.bill_id == bill.id, ItemRow.deleted_at.is_(None))
    )
    card: dict[str, Any] = {
        "id": str(bill.id),
        "store_name": bill.store_name,
        "purchase_date": bill.purchase_date.isoformat(),
        "state": bill.state,
        "payer_member_id": str(bill.payer_member_id),
        "payer_name": names.get(bill.payer_member_id),
        "total_cents": bill.total_cents,
        "item_count": int(item_count or 0),
        "version": bill.version,
        "settled_at": bill.settled_at.isoformat() if bill.settled_at else None,
    }
    if bill.state == "open":
        rows = await bill_math.load(db, bill)
        payments = await payments_for(db, bill.id)
        owed = owed_map(rows, payments) if not rows.unassigned else {}
        debtors = [k for k, v in owed.items() if k.startswith("m:")]
        paid = [k for k in debtors if owed[k] <= 0]
        card["paid_count"] = len(paid)
        card["debtor_count"] = len(debtors)
        card["my_owed_cents"] = owed.get(f"m:{me.member_id}", 0)
        card["changed"] = await changed_since_seen(db, me.user_id, me.member_id, bill)
        last = max((p.created_at for p in payments), default=None)
        card["last_activity"] = last.isoformat() if last else None
    return card


@router.get("/houses/{house_id}/tabs")
async def tabs(
    house_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)], cursor: str | None = None
) -> dict[str, Any]:
    """Drafts I can finish, open tabs (newest first) and settled history, 20 per page (FR-52)."""
    me = await _member_or_403(request, house_id, actor)
    async with ctx(request).sessions() as db:
        names = {
            m.id: m.display_name for m in (await db.scalars(select(Member).where(Member.house_id == house_id))).all()
        }
        base = select(Bill).where(Bill.house_id == house_id, Bill.deleted_at.is_(None))
        drafts = (
            await db.scalars(
                base.where(
                    Bill.state == "draft", or_(Bill.created_by == actor.user.id, Bill.payer_member_id == me.member_id)
                ).order_by(Bill.created_at.desc())
            )
        ).all()
        open_bills = (
            await db.scalars(
                base.where(Bill.state == "open").order_by(Bill.purchase_date.desc(), Bill.created_at.desc())
            )
        ).all()
        history_q = base.where(Bill.state.in_(("settled", "voided")))
        if cursor:
            at_raw, _, id_raw = cursor.partition("|")
            at, cid = date.fromisoformat(at_raw), uuid.UUID(id_raw)
            history_q = history_q.where(or_(Bill.purchase_date < at, and_(Bill.purchase_date == at, Bill.id < cid)))
        history = (
            await db.scalars(history_q.order_by(Bill.purchase_date.desc(), Bill.id.desc()).limit(PAGE + 1))
        ).all()
        page = history[:PAGE]
        return {
            "drafts": [await _card(db, b, me, names) for b in drafts],
            "open": [await _card(db, b, me, names) for b in open_bills],
            "history": [await _card(db, b, me, names) for b in page],
            "next_cursor": f"{page[-1].purchase_date.isoformat()}|{page[-1].id}" if len(history) > PAGE else None,
        }


@router.get("/houses/{house_id}/search")
async def search(
    house_id: uuid.UUID, q: str, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> list[dict[str, Any]]:
    """FR-53: bills by store or item name (trigram-indexed ILIKE)."""
    await _member_or_403(request, house_id, actor)
    term = q.strip()
    if not 2 <= len(term) <= 60:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Search for 2 to 60 characters")
    pattern = f"%{term.replace('%', '').replace('_', '')}%"
    async with ctx(request).sessions() as db:
        item_bills = select(ItemRow.bill_id).where(ItemRow.name.ilike(pattern), ItemRow.deleted_at.is_(None))
        rows = (
            await db.scalars(
                select(Bill)
                .where(
                    Bill.house_id == house_id,
                    Bill.deleted_at.is_(None),
                    Bill.state != "draft",
                    or_(Bill.store_name.ilike(pattern), Bill.id.in_(item_bills)),
                )
                .order_by(Bill.purchase_date.desc())
                .limit(50)
            )
        ).all()
    return [
        {
            "id": str(b.id),
            "store_name": b.store_name,
            "purchase_date": b.purchase_date.isoformat(),
            "state": b.state,
            "total_cents": b.total_cents,
        }
        for b in rows
    ]


@router.get("/bills/{bill_id}/export.csv")
async def export_csv(bill_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Response:
    """FR-54: one row per item x participant allocation, amounts in dollars with 2 decimals."""
    async with ctx(request).sessions() as db:
        bill = await db.get(Bill, bill_id)
        if bill is None or bill.deleted_at is not None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
        me = await membership(db, bill.house_id, actor.user.id)
        require(Action.BILL_READ, actor=me, bill=bill_svc.bill_ctx(bill))
        rows = await bill_math.load(db, bill)
        result = bill_math.shares(rows) if not rows.unassigned else bill_math.preview(rows)
    names = {f"m:{m.id}": m.display_name for m in rows.members} | {f"g:{g.id}": g.display_name for g in rows.guests}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["bill_date", "store", "item", "item_total", "person", "person_item_share"])
    for item in rows.items:
        for pid, cents in result.by_item.get(str(item.id), {}).items():
            writer.writerow(
                [
                    bill.purchase_date.isoformat(),
                    bill.store_name or "",
                    item.name,
                    _dollars(item.line_total_cents),
                    names.get(pid, pid),
                    _dollars(cents),
                ]
            )
    for pid, cents in result.shares.items():
        if cents:
            writer.writerow(
                [
                    bill.purchase_date.isoformat(),
                    bill.store_name or "",
                    "TOTAL",
                    _dollars(bill.total_cents),
                    names.get(pid, pid),
                    _dollars(cents),
                ]
            )
    filename = f"kitchentab-{(bill.store_name or 'tab').lower().replace(' ', '-')}-{bill.purchase_date}.csv"
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{whole}.{part:02d}"
