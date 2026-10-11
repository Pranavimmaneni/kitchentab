"""Fridge and pantry endpoints (PRD §19.7)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select

from app.deps import Actor, client_mutation_id, ctx, idempotency, if_match, require_actor
from app.errors import ErrorCode, Problem
from app.models import House, InventoryItem, InventoryOwner, Member, ShareRequest
from app.policy import Action, require
from app.services import inventory as svc
from app.services.house_write import house_write
from app.services.houses import membership
from app.services.kitchen import _share_defaults as share_defaults

router = APIRouter(tags=["kitchen"])


async def _house_and_me(request: Request, house_id: uuid.UUID, actor: Actor) -> tuple[House, Any]:
    async with ctx(request).sessions() as db:
        me = await membership(db, house_id, actor.user.id)
        require(Action.HOUSE_READ, actor=me)
        house = await db.get(House, house_id)
    if house is None:
        raise Problem(404, ErrorCode.NOT_FOUND, "House not found")
    return house, me


@router.get("/houses/{house_id}/kitchen")
async def kitchen(
    house_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    house, me = await _house_and_me(request, house_id, actor)
    c = ctx(request)
    async with c.read_sessions() as db:
        return await svc.kitchen_view(db, house, me, c.clock.now())


@router.get("/houses/{house_id}/inventory")
async def inventory(
    house_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    place: Literal["fridge", "pantry"] = "fridge",
    filter: Literal["all", "mine", "open", "use_soon"] = "all",
) -> dict[str, Any]:
    """K2/K3: every in-stock item of the place with counts per filter (FR-65..FR-67). The filter only
    marks matches; the UI dims the rest to 22% so nothing moves."""
    house, me = await _house_and_me(request, house_id, actor)
    c = ctx(request)
    places = ("fridge", "freezer") if place == "fridge" else ("pantry",)
    async with c.read_sessions() as db:
        items = list(
            (
                await db.scalars(
                    select(InventoryItem)
                    .where(
                        InventoryItem.house_id == house_id,
                        InventoryItem.status == "in_stock",
                        InventoryItem.place.in_(places),
                    )
                    .order_by(InventoryItem.category, InventoryItem.stocked_on.desc(), InventoryItem.created_at.desc())
                )
            ).all()
        )
        views = await svc.item_views(db, house, me, items, c.clock.now())
    key = {"all": None, "mine": "mine", "open": "open_for_me", "use_soon": "use_soon"}[filter]
    for v in views:
        v["matches"] = True if key is None else bool(v[key])
    return {
        "place": place,
        "filter": filter,
        "counts": svc.filter_counts(views),
        "areas": [{"area": area, "items": [v for v in views if v["place"] == area]} for area in places],
    }


class Leftovers(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    place: Literal["fridge", "freezer", "pantry"] = "fridge"
    category: str | None = Field(default=None, max_length=20)
    icon_key: str | None = Field(default=None, max_length=30)
    shelf_life_days: Annotated[int, Field(gt=0, le=3650)] | None = None
    owner_member_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    open_to_share: bool = False


@router.post("/houses/{house_id}/inventory", status_code=201)
async def add_leftovers(
    house_id: uuid.UUID, body: Leftovers, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.add_manual(
        c.writes,
        house_id,
        actor.user.id,
        body.model_dump(mode="json"),
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )


class ItemEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    place: Literal["fridge", "freezer", "pantry"] | None = None
    shelf_life_days: Annotated[int, Field(gt=0, le=3650)] | None = None
    owner_member_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)  # manual items only


@router.patch("/inventory/{item_id}")
async def edit_item(
    item_id: uuid.UUID, body: ItemEdit, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    """Guarded edit by owners (If-Match). Owners of bill-backed items follow the bill (§19.2)."""
    c = ctx(request)
    expected = if_match(request)
    now = c.clock.now()
    async with c.sessions() as db:
        house_id = await svc.item_house(db, item_id)
    async with house_write(c.writes, house_id, client_mutation_id=client_mutation_id(request)) as w:
        me = await svc._me(w, actor.user.id)
        item = await svc._lock_item(w, item_id)
        svc._require_owner(me, await svc._active_owner_ids(w.session, item.id))
        if item.version != expected:
            raise Problem(
                409, ErrorCode.VERSION_CONFLICT, "Someone just changed this", extra={"current": svc._event_view(item)}
            )
        if body.name:
            from app.domain.kitchen import short_name

            item.name, item.short_name = body.name, short_name(body.name)
        if body.place:
            item.place = body.place
        if body.shelf_life_days:
            item.shelf_life_days = body.shelf_life_days
        if body.owner_member_ids is not None:
            if item.bill_id is not None:
                raise Problem(409, ErrorCode.CONFLICT, "Owners of a tab item follow the tab")
            current = {
                o.member_id: o
                for o in (
                    await w.session.scalars(select(InventoryOwner).where(InventoryOwner.inventory_item_id == item.id))
                ).all()
            }
            wanted = set(await svc.house_owner_ids(w, list(body.owner_member_ids), me))
            defaults = await share_defaults(w.session, sorted(wanted - set(current)))
            for mid in wanted - set(current):
                w.session.add(
                    InventoryOwner(
                        inventory_item_id=item.id,
                        member_id=mid,
                        open_to_share=defaults.get(mid, False),
                        updated_by_member_id=me.member_id,
                        updated_at=now,
                    )
                )
            for mid in set(current) - wanted:
                await w.session.delete(current[mid])
        item.version += 1
        item.updated_at = now
        w.emit("inventory_changed", aggregate=("inventory", item.id, item.version), data=svc._event_view(item))
        return svc._event_view(item)


class Sharing(BaseModel):
    open: bool


@router.put("/inventory/{item_id}/sharing")
async def sharing(
    item_id: uuid.UUID, body: Sharing, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.set_sharing(
        c.writes, item_id, actor.user.id, body.open, c.clock.now(), client_mutation_id(request)
    )


@router.post("/inventory/{item_id}/used-up")
async def used_up(item_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.used_up(
        c.writes, item_id, actor.user.id, c.clock.now(), idempotency(request, actor, None), client_mutation_id(request)
    )


@router.post("/inventory/{item_id}/restore")
async def restore(item_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.restore(
        c.writes, item_id, actor.user.id, c.clock.now(), idempotency(request, actor, None), client_mutation_id(request)
    )


@router.post("/inventory/{item_id}/still-good")
async def still_good(item_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.still_good(
        c.writes, item_id, actor.user.id, c.clock.now(), idempotency(request, actor, None), client_mutation_id(request)
    )


class Ask(BaseModel):
    amount: Literal["a_bit", "half", "all"]
    note: str | None = Field(default=None, max_length=140)


@router.post("/inventory/{item_id}/requests", status_code=201)
async def ask(item_id: uuid.UUID, body: Ask, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.ask(
        c.writes,
        item_id,
        actor.user.id,
        body.amount,
        body.note,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


@router.get("/houses/{house_id}/requests")
async def requests(
    house_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    box: Literal["in", "out"] = "in",
) -> dict[str, Any]:
    """K4 (FR-75): FOR YOU (asks on my items) / YOU ASKED; pending first, newest first; counts."""
    _house, me = await _house_and_me(request, house_id, actor)
    c = ctx(request)
    now = c.clock.now()
    async with c.read_sessions() as db:
        mine = select(InventoryOwner.inventory_item_id).where(InventoryOwner.member_id == me.member_id)
        base = select(ShareRequest).where(
            ShareRequest.house_id == house_id, ShareRequest.created_at > now - timedelta(days=30)
        )
        incoming = (
            await db.scalars(
                base.where(ShareRequest.inventory_item_id.in_(mine), ShareRequest.requester_member_id != me.member_id)
            )
        ).all()
        outgoing = (await db.scalars(base.where(ShareRequest.requester_member_id == me.member_id))).all()
        rows = incoming if box == "in" else outgoing
        rows = sorted(rows, key=lambda r: (svc.effective_status(r, now) != "pending", -r.created_at.timestamp()))
        items = {
            i.id: i
            for i in (
                await db.scalars(select(InventoryItem).where(InventoryItem.id.in_({r.inventory_item_id for r in rows})))
            ).all()
        }
        members = {m.id: m for m in (await db.scalars(select(Member).where(Member.house_id == house_id))).all()}
        out = []
        for r in rows:
            view = svc.request_view(
                r,
                items[r.inventory_item_id],
                members.get(r.requester_member_id),
                members.get(r.answered_by_member_id) if r.answered_by_member_id else None,
            )
            owners = await svc._active_owner_ids(db, r.inventory_item_id)
            bill, line = await svc._source_line(db, items[r.inventory_item_id])
            # K4 copy: who owns it, which tab it came from, and the chip-in ceiling (§19.4, §19.8).
            view["owners"] = [members[o].display_name for o in owners if o in members]
            view["source"] = {"store_name": bill.store_name, "state": bill.state} if bill else None
            view["line_total_cents"] = line.line_total_cents if line else items[r.inventory_item_id].price_cents
            view["status"] = svc.effective_status(r, now)
            if box == "in" and view["status"] == "pending":
                view["options"] = (
                    ["free"] if bill is None else ["free", "split"] if bill.state == "open" else ["free", "chip_in"]
                )
                view["split_deltas"] = (
                    await svc.split_preview(db, r, items[r.inventory_item_id]) if "split" in view["options"] else {}
                )
                view["chip_in_suggestion_cents"] = (
                    await svc.chip_in_suggestion(db, items[r.inventory_item_id], owners)
                    if "chip_in" in view["options"]
                    else None
                )
                view["co_owners"] = [members[o].display_name for o in owners if o != me.member_id]
            out.append(view)
    return {
        "box": box,
        "requests": out,
        "counts": {
            "in": sum(svc.effective_status(r, now) == "pending" for r in incoming),
            "out": sum(svc.effective_status(r, now) == "pending" for r in outgoing),
        },
    }


@router.get("/requests/{request_id}/split-preview")
async def split_preview(
    request_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, int]:
    async with ctx(request).read_sessions() as db:
        req = await db.get(ShareRequest, request_id)
        if req is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "That ask is gone")
        me = await membership(db, req.house_id, actor.user.id)
        require(Action.HOUSE_READ, actor=me)
        item = await db.get(InventoryItem, req.inventory_item_id)
        assert item is not None
        return await svc.split_preview(db, req, item)


Percent = Annotated[int, Field(ge=1, le=100)]
Cents = Annotated[int, Field(gt=0, le=10_000_000)]


class Answer(BaseModel):
    decision: Literal["decline", "free", "split", "chip_in"]
    chip_in_cents: Cents | None = None
    # FR-81 custom split (split or chip_in): a percent of the line, or the owner's own value.
    percent: Percent | None = None
    amount_cents: Cents | None = None

    @model_validator(mode="after")
    def _one_amount(self) -> Answer:
        if sum(v is not None for v in (self.chip_in_cents, self.percent, self.amount_cents)) > 1:
            raise ValueError("Give a percent or an amount, not both")
        return self


@router.post("/requests/{request_id}/answer")
async def answer(
    request_id: uuid.UUID, body: Answer, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.answer(
        c.writes,
        request_id,
        actor.user.id,
        body.decision,
        body.chip_in_cents,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
        svc.CustomSplit(body.percent, body.amount_cents),
    )


@router.get("/requests/{request_id}/answer-preview")
async def answer_preview(
    request_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
    decision: Literal["split", "chip_in"] = "split",
    percent: Annotated[int | None, Query(ge=1, le=100)] = None,
    amount_cents: Annotated[int | None, Query(gt=0, le=10_000_000)] = None,
) -> dict[str, Any]:
    """FR-81: the asker's part and every share delta for a custom split, before saying yes."""
    if percent is not None and amount_cents is not None:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Give a percent or an amount, not both")
    async with ctx(request).read_sessions() as db:
        req = await db.get(ShareRequest, request_id)
        if req is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "That ask is gone")
        me = await membership(db, req.house_id, actor.user.id)
        require(Action.HOUSE_READ, actor=me)
        item = await db.get(InventoryItem, req.inventory_item_id)
        assert item is not None
        return await svc.answer_preview(db, req, item, decision, svc.CustomSplit(percent, amount_cents))


@router.post("/requests/{request_id}/undo")
async def undo(request_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.undo_answer(
        c.writes,
        request_id,
        actor.user.id,
        c.clock.now(),
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


@router.post("/requests/{request_id}/cancel")
async def cancel(request_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.cancel(
        c.writes,
        request_id,
        actor.user.id,
        c.clock.now(),
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


@router.get("/houses/{house_id}/kitchen/history")
async def history(
    house_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    """FR-80: used-up items and answered asks from the last 30 days."""
    _, me = await _house_and_me(request, house_id, actor)
    c = ctx(request)
    since = c.clock.now() - timedelta(days=30)
    async with c.read_sessions() as db:
        used = (
            await db.scalars(
                select(InventoryItem)
                .where(
                    InventoryItem.house_id == house_id,
                    InventoryItem.status == "used_up",
                    InventoryItem.used_up_at > since,
                )
                .order_by(InventoryItem.used_up_at.desc())
            )
        ).all()
        answered = (
            await db.scalars(
                select(ShareRequest)
                .where(
                    ShareRequest.house_id == house_id,
                    ShareRequest.answered_at > since,
                    or_(ShareRequest.status == "approved", ShareRequest.status == "declined"),
                )
                .order_by(ShareRequest.answered_at.desc())
            )
        ).all()
    return {
        "used_up": [
            {"id": str(i.id), "name": i.name, "used_up_at": i.used_up_at.isoformat() if i.used_up_at else None}
            for i in used
        ],
        "answered": [
            {
                "id": str(r.id),
                "status": r.status,
                "resolution": r.resolution,
                "answered_at": r.answered_at.isoformat() if r.answered_at else None,
            }
            for r in answered
        ],
        "me": str(me.member_id),
    }
