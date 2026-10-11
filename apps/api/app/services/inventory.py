"""Fridge and pantry (PRD §19; FR-63..FR-80): views, sharing, used up, asks and answers.

Derived values are computed here, never stored (§19.7):
  is_open             every owner said yes (no owners left: open to everyone, EDGE-19-02)
  visible_open_for(m) is_open or m holds a grant from an approved ask
  age_ratio           (today in house tz - stocked_on) / (shelf_life_days + snooze days)
  use_soon            age_ratio >= 0.8          probably_gone   age_ratio >= 1.0
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.kitchen import default_shelf_life, generic_icon, short_name
from app.domain.money import largest_remainder
from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.models import (
    Allocation,
    Bill,
    House,
    InventoryGrant,
    InventoryItem,
    InventoryOwner,
    LedgerEntry,
    Member,
    ShareRequest,
)
from app.models import Item as ItemRow
from app.policy import Action, AskCtx, ItemCtx, MemberCtx, require
from app.services import bill_math
from app.services.bill_math import mkey
from app.services.house_write import Activity, HouseWrite, Idempotency, WriteContext, house_write
from app.services.houses import membership
from app.services.kitchen import _share_defaults

USE_SOON_X1000 = 800  # 80% of shelf life (integer ratio, no floats near prices)
PROBABLY_GONE_X1000 = 1000
STILL_GOOD_DAYS = 3
ASK_TTL = timedelta(hours=48)
UNDO_WINDOW = timedelta(minutes=5)
RESTORE_WINDOW = timedelta(seconds=60)  # UI offers 5 s; the server allows 60 s (§19.7)
ASKS_PER_ITEM_PER_WEEK = 3
MAX_PENDING_OUTGOING = 10
AMOUNT_WORDS = {"a_bit": "a bit of", "half": "half of", "all": "all of"}


def house_today(house: House, now: datetime) -> date:
    return now.astimezone(ZoneInfo(house.timezone)).date()


@dataclass(frozen=True)
class Freshness:
    age_days: int
    expiry: date
    days_left: int
    ratio_x1000: int  # integer ratio, no floats

    @property
    def use_soon(self) -> bool:
        return self.ratio_x1000 >= USE_SOON_X1000

    @property
    def probably_gone(self) -> bool:
        return self.ratio_x1000 >= PROBABLY_GONE_X1000

    @property
    def label(self) -> str | None:
        if self.probably_gone:
            return "STILL THERE?"
        if not self.use_soon:
            return None
        return (
            "USE TODAY"
            if self.days_left <= 0
            else "1 DAY LEFT"
            if self.days_left == 1
            else f"{self.days_left} DAYS LEFT"
        )


def freshness(item: InventoryItem, today: date) -> Freshness:
    base_expiry = item.stocked_on + timedelta(days=item.shelf_life_days)
    expiry = max(base_expiry, item.snoozed_until) if item.snoozed_until else base_expiry
    span = max((expiry - item.stocked_on).days, 1)
    age = (today - item.stocked_on).days
    return Freshness(age, expiry, (expiry - today).days, age * 1000 // span)


# ---- reads ----------------------------------------------------------------------------------------


async def _owners(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[InventoryOwner]]:
    out: dict[uuid.UUID, list[InventoryOwner]] = {i: [] for i in ids}
    if ids:
        for o in (await db.scalars(select(InventoryOwner).where(InventoryOwner.inventory_item_id.in_(ids)))).all():
            out[o.inventory_item_id].append(o)
    return out


async def _grants(db: AsyncSession, ids: list[uuid.UUID], member_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    """Items this member was granted -> the owner who said yes ("approved by Pranav", §19.3)."""
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(InventoryGrant.inventory_item_id, InventoryGrant.granted_by_member_id).where(
                InventoryGrant.inventory_item_id.in_(ids), InventoryGrant.member_id == member_id
            )
        )
    ).all()
    return {item_id: granted_by for item_id, granted_by in rows}


async def _my_pending(
    db: AsyncSession, ids: list[uuid.UUID], member_id: uuid.UUID, now: datetime
) -> dict[uuid.UUID, ShareRequest]:
    if not ids:
        return {}
    rows = (
        await db.scalars(
            select(ShareRequest).where(
                ShareRequest.inventory_item_id.in_(ids),
                ShareRequest.requester_member_id == member_id,
                ShareRequest.status == "pending",
                ShareRequest.expires_at > now,
            )
        )
    ).all()
    return {r.inventory_item_id: r for r in rows}


def _member_chip(m: Member) -> dict[str, Any]:
    return {"id": str(m.id), "display_name": m.display_name, "initial": m.display_name[:1].upper(), "color": m.color}


async def item_views(
    db: AsyncSession, house: House, me: MemberCtx, items: list[InventoryItem], now: datetime
) -> list[dict[str, Any]]:
    ids = [i.id for i in items]
    owners = await _owners(db, ids)
    grants = await _grants(db, ids, me.member_id)
    pending = await _my_pending(db, ids, me.member_id, now)
    members = {m.id: m for m in (await db.scalars(select(Member).where(Member.house_id == house.id))).all()}
    bills = {
        b.id: b
        for b in (await db.scalars(select(Bill).where(Bill.id.in_({i.bill_id for i in items if i.bill_id})))).all()
    }
    today = house_today(house, now)
    out = []
    for item in items:
        # In house order, so "Pranav & Arjun" reads the same on every load (KITCH-08).
        its_owners = sorted(
            (o for o in owners[item.id] if members[o.member_id].status == "active"),
            key=lambda o: (members[o.member_id].sort_order, str(o.member_id)),
        )
        is_open = all(o.open_to_share for o in its_owners)  # no active owners: open to everyone
        mine = any(o.member_id == me.member_id for o in its_owners)
        fresh = freshness(item, today)
        source = bills.get(item.bill_id) if item.bill_id else None
        out.append(
            {
                "id": str(item.id),
                "name": item.name,
                "short_name": item.short_name,
                "icon_key": item.icon_key,
                "place": item.place,
                "category": item.category,
                "price_cents": item.price_cents,
                "stocked_on": item.stocked_on.isoformat(),
                "shelf_life_days": item.shelf_life_days,
                "status": item.status,
                "version": item.version,
                "source": {
                    "bill_id": str(source.id),
                    "store_name": source.store_name,
                    "purchase_date": source.purchase_date.isoformat(),
                    "state": source.state,
                }
                if source
                else None,
                "owners": [
                    {**_member_chip(members[o.member_id]), "open_to_share": o.open_to_share} for o in its_owners
                ],
                "holdouts": [members[o.member_id].display_name for o in its_owners if not o.open_to_share],
                "is_open": is_open,
                "open_for_me": is_open or item.id in grants or mine,
                "granted_to_me": item.id in grants,
                "granted_by": members[grants[item.id]].display_name
                if item.id in grants and grants[item.id] in members
                else None,
                "mine": mine,
                "use_soon": fresh.use_soon,
                "probably_gone": fresh.probably_gone,
                "days_left": fresh.days_left,
                "soon_label": fresh.label,
                "my_request": {"id": str(pending[item.id].id), "amount": pending[item.id].amount}
                if item.id in pending
                else None,
            }
        )
    return out


async def kitchen_view(db: AsyncSession, house: House, me: MemberCtx, now: datetime) -> dict[str, Any]:
    """K1 Kitchen (FR-63, FR-64): counts per place, use-soon items per place, asks for me."""
    items = list(
        (
            await db.scalars(
                select(InventoryItem).where(InventoryItem.house_id == house.id, InventoryItem.status == "in_stock")
            )
        ).all()
    )
    views = await item_views(db, house, me, items, now)
    panels = {}
    for panel, places in (("fridge", ("fridge", "freezer")), ("pantry", ("pantry",))):
        in_panel = [v for v in views if v["place"] in places]
        soon = sorted((v for v in in_panel if v["use_soon"]), key=lambda v: (v["days_left"], v["name"]))
        panels[panel] = {"count": len(in_panel), "use_soon": soon}
    asks = await db.scalar(
        select(func.count())
        .select_from(ShareRequest)
        .join(InventoryOwner, InventoryOwner.inventory_item_id == ShareRequest.inventory_item_id)
        .where(
            ShareRequest.house_id == house.id,
            ShareRequest.status == "pending",
            ShareRequest.expires_at > now,
            InventoryOwner.member_id == me.member_id,
        )
    )
    return {"house": {"id": str(house.id), "name": house.name}, "panels": panels, "asks_for_me": int(asks or 0)}


def filter_counts(views: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "all": len(views),
        "mine": sum(v["mine"] for v in views),
        "open": sum(v["open_for_me"] for v in views),
        "use_soon": sum(v["use_soon"] for v in views),
    }


# ---- helpers for writes ------------------------------------------------------------------------


async def _me(w: HouseWrite, user_id: uuid.UUID) -> MemberCtx:
    me = await membership(w.session, w.house_id, user_id)
    if me is None or not me.active:
        raise Problem(403, ErrorCode.NOT_A_MEMBER, "You're not in this house")
    w.actor_member_id = me.member_id
    return me


async def item_house(db: AsyncSession, item_id: uuid.UUID) -> uuid.UUID:
    house_id = await db.scalar(select(InventoryItem.house_id).where(InventoryItem.id == item_id))
    if house_id is None:
        raise Problem(404, ErrorCode.NOT_FOUND, "That item isn't in the kitchen")
    return house_id


async def _lock_item(w: HouseWrite, item_id: uuid.UUID) -> InventoryItem:
    item = (
        await w.session.scalars(
            select(InventoryItem).where(InventoryItem.id == item_id).with_for_update(key_share=True)
        )
    ).first()
    if item is None or item.house_id != w.house_id:
        raise Problem(404, ErrorCode.NOT_FOUND, "That item isn't in the kitchen")
    return item


async def _active_owner_ids(db: AsyncSession, item_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await db.scalars(
                select(InventoryOwner.member_id)
                .join(Member, Member.id == InventoryOwner.member_id)
                .where(InventoryOwner.inventory_item_id == item_id, Member.status == "active")
                .order_by(Member.sort_order, Member.id)
            )
        ).all()
    )


async def house_owner_ids(w: HouseWrite, wanted: list[uuid.UUID], me: MemberCtx) -> list[uuid.UUID]:
    """Owners named for a manual item: active members of THIS house only (else 422); none = me."""
    if not wanted:
        return [me.member_id]
    found = set(
        (
            await w.session.scalars(
                select(Member.id).where(Member.id.in_(wanted), Member.house_id == w.house_id, Member.status == "active")
            )
        ).all()
    )
    if found != set(wanted):
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Owners must be people who live here")
    return sorted(found, key=wanted.index)


def _require_owner(me: MemberCtx, owner_ids: list[uuid.UUID]) -> None:
    require(Action.ITEM_OWNER_ACT, actor=me, item=ItemCtx(frozenset(owner_ids)))


def _event_view(item: InventoryItem) -> dict[str, Any]:
    return {"id": str(item.id), "status": item.status, "place": item.place, "version": item.version}


# ---- set-state and simple transitions --------------------------------------------------------------


async def set_sharing(
    writes: WriteContext, item_id: uuid.UUID, user_id: uuid.UUID, open_: bool, now: datetime, cmid: str | None
) -> dict[str, Any]:
    """FR-69: my own consent for this item (set-state)."""
    async with writes.sessions() as db:
        house_id = await item_house(db, item_id)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _me(w, user_id)
        item = await _lock_item(w, item_id)
        _require_owner(me, await _active_owner_ids(w.session, item.id))
        owner = await w.session.get(InventoryOwner, (item.id, me.member_id), with_for_update={"key_share": True})
        assert owner is not None
        if owner.open_to_share == open_:
            return {"id": str(item_id), "open_to_share": open_}
        owner.open_to_share = open_
        owner.updated_by_member_id = me.member_id
        owner.updated_at = now
        w.emit(
            "inventory_changed",
            aggregate=("inventory", item.id, item.version),
            data={"id": str(item.id), "sharing": {str(me.member_id): open_}},
        )
        return {"id": str(item_id), "open_to_share": open_}


async def used_up(
    writes: WriteContext, item_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    """FR-77: any owner; idempotent (SYNC-13); pending asks close as expired; kept in history."""
    async with writes.sessions() as db:
        house_id = await item_house(db, item_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        item = await _lock_item(w, item_id)
        _require_owner(me, await _active_owner_ids(w.session, item.id))
        if item.status == "in_stock":
            item.status = "used_up"
            item.used_up_by_member_id = me.member_id
            item.used_up_at = now
            item.updated_at = now
            item.version += 1
            closed = (
                await w.session.scalars(
                    update(ShareRequest)
                    .where(ShareRequest.inventory_item_id == item.id, ShareRequest.status == "pending")
                    .values(status="expired", version=ShareRequest.version + 1)
                    .returning(ShareRequest.id)
                )
            ).all()
            w.emit(
                "inventory_changed",
                aggregate=("inventory", item.id, item.version),
                data={**_event_view(item), "closed_requests": [str(i) for i in closed]},
                activity=Activity("used_up", payload={"name": item.name}, actor_user_id=user_id),
            )
        out = {
            **_event_view(item),
            "restore_until": (item.used_up_at + RESTORE_WINDOW).isoformat() if item.used_up_at else None,
        }
        w.respond(200, out)
    return out


async def restore(
    writes: WriteContext, item_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    """Undo "Used up" (UI 5 s, server allows 60 s)."""
    async with writes.sessions() as db:
        house_id = await item_house(db, item_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        item = await _lock_item(w, item_id)
        _require_owner(me, await _active_owner_ids(w.session, item.id))
        if item.status != "used_up" or item.used_up_at is None or now - item.used_up_at > RESTORE_WINDOW:
            raise Problem(409, ErrorCode.CONFLICT, "Too late to undo")
        item.status, item.used_up_at, item.used_up_by_member_id = "in_stock", None, None
        item.updated_at = now
        item.version += 1
        w.emit(
            "inventory_changed",
            aggregate=("inventory", item.id, item.version),
            data=_event_view(item),
            activity=Activity("used_up_undone", payload={"name": item.name}, actor_user_id=user_id),
        )
        out = _event_view(item)
        w.respond(200, out)
    return out


async def still_good(
    writes: WriteContext, item_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    """FR-78: "Still good" pushes the nudge back 3 days; items are never removed automatically."""
    async with writes.sessions() as db:
        house_id = await item_house(db, item_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        item = await _lock_item(w, item_id)
        _require_owner(me, await _active_owner_ids(w.session, item.id))
        house = await w.session.get(House, house_id)
        assert house is not None
        today = house_today(house, now)
        item.snoozed_until = max(freshness(item, today).expiry, today) + timedelta(days=STILL_GOOD_DAYS)
        item.updated_at = now
        item.version += 1
        w.emit("inventory_changed", aggregate=("inventory", item.id, item.version), data=_event_view(item))
        out = {**_event_view(item), "snoozed_until": item.snoozed_until.isoformat()}
        w.respond(200, out)
    return out


async def add_manual(
    writes: WriteContext,
    house_id: uuid.UUID,
    user_id: uuid.UUID,
    body: dict[str, Any],
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-79: leftovers / something manual: no bill, no money; creator owns it by default."""
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        house = await w.session.get(House, house_id)
        assert house is not None
        name = " ".join(str(body["name"]).split())[:80]
        if not name:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "Give it a name")
        place = body.get("place") or "fridge"
        category = body.get("category") or ("prepared" if place != "pantry" else "other")
        icon = body.get("icon_key") or ("leftovers" if place != "pantry" else generic_icon(place))
        item = InventoryItem(
            id=uuid7(),
            house_id=house_id,
            place=place,
            category=category,
            icon_key=icon,
            name=name,
            short_name=short_name(name),
            stocked_on=house_today(house, now),
            shelf_life_days=int(body.get("shelf_life_days") or default_shelf_life(category, icon)),
            created_by=me.member_id,
            created_at=now,
            updated_at=now,
        )
        w.session.add(item)
        await w.session.flush()
        owner_ids = await house_owner_ids(w, [uuid.UUID(str(o)) for o in body.get("owner_member_ids") or []], me)
        defaults = await _share_defaults(w.session, owner_ids)
        for mid in owner_ids:
            w.session.add(
                InventoryOwner(
                    inventory_item_id=item.id,
                    member_id=mid,
                    # §19.3: each owner's consent is their own; others start from their FR-70 default
                    open_to_share=bool(body.get("open_to_share", False))
                    if mid == me.member_id
                    else defaults.get(mid, False),
                    updated_by_member_id=me.member_id,
                    updated_at=now,
                )
            )
        w.emit(
            "inventory_changed",
            aggregate=("inventory", item.id, 1),
            data=_event_view(item),
            activity=Activity("leftovers_added", payload={"name": name}, actor_user_id=user_id),
        )
        out = _event_view(item)
        w.respond(201, out)
    return out


# ---- asks -------------------------------------------------------------------------------------


async def ask(
    writes: WriteContext,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    amount: str,
    note: str | None,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-71: non-owners ask for a LOCKED item; limits enforced here (§19.4.5)."""
    async with writes.sessions() as db:
        house_id = await item_house(db, item_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        item = await _lock_item(w, item_id)
        if item.status != "in_stock":
            raise Problem(410, ErrorCode.ITEM_GONE, "That's been used up")
        owner_rows = (
            await w.session.scalars(
                select(InventoryOwner)
                .join(Member, Member.id == InventoryOwner.member_id)
                .where(InventoryOwner.inventory_item_id == item.id, Member.status == "active")
            )
        ).all()
        require(Action.ASK_CREATE, actor=me, item=ItemCtx(frozenset(o.member_id for o in owner_rows)))
        granted = await w.session.get(InventoryGrant, (item.id, me.member_id))
        if all(o.open_to_share for o in owner_rows) or granted is not None:
            raise Problem(409, ErrorCode.CONFLICT, "No need to ask: help yourself")
        mine = select(ShareRequest).where(ShareRequest.requester_member_id == me.member_id)
        if (
            await w.session.scalars(
                mine.where(
                    ShareRequest.inventory_item_id == item.id,
                    ShareRequest.status == "pending",
                    ShareRequest.expires_at > now,
                )
            )
        ).first():
            raise Problem(409, ErrorCode.CONFLICT, "You already asked for this")
        recent = await w.session.scalar(
            select(func.count())
            .select_from(ShareRequest)
            .where(
                ShareRequest.requester_member_id == me.member_id,
                ShareRequest.inventory_item_id == item.id,
                ShareRequest.created_at > now - timedelta(days=7),
            )
        )
        if (recent or 0) >= ASKS_PER_ITEM_PER_WEEK:
            raise Problem(429, ErrorCode.RATE_LIMITED, "You've asked for this a few times this week")
        outgoing = await w.session.scalar(
            select(func.count())
            .select_from(ShareRequest)
            .where(
                ShareRequest.requester_member_id == me.member_id,
                ShareRequest.status == "pending",
                ShareRequest.expires_at > now,
            )
        )
        if (outgoing or 0) >= MAX_PENDING_OUTGOING:
            raise Problem(429, ErrorCode.RATE_LIMITED, "You have 10 asks waiting already")
        # Expired-but-unswept pending rows would block the partial unique index: close them first.
        await w.session.execute(
            update(ShareRequest)
            .where(
                ShareRequest.inventory_item_id == item.id,
                ShareRequest.requester_member_id == me.member_id,
                ShareRequest.status == "pending",
            )
            .values(status="expired")
        )
        req = ShareRequest(
            id=uuid7(),
            house_id=house_id,
            inventory_item_id=item.id,
            requester_member_id=me.member_id,
            amount=amount,
            note=(note or None),
            expires_at=now + ASK_TTL,
            created_at=now,
        )
        w.session.add(req)
        await w.session.flush()
        w.emit(
            "request_created",
            aggregate=("share_request", req.id, 1),
            data={"id": str(req.id), "item_id": str(item.id), "requester": str(me.member_id)},
            activity=Activity("ask_created", payload={"item": item.name, "amount": amount}, actor_user_id=user_id),
            notify=["asks"],
        )
        out = request_view(req, item, None, None)
        w.respond(201, out)
    return out


def request_view(
    req: ShareRequest, item: InventoryItem, requester: Member | None, answered_by: Member | None
) -> dict[str, Any]:
    return {
        "id": str(req.id),
        "item": {
            "id": str(item.id),
            "name": item.name,
            "short_name": item.short_name,
            "icon_key": item.icon_key,
            "bill_id": str(item.bill_id) if item.bill_id else None,
        },
        "requester": _member_chip(requester) if requester else {"id": str(req.requester_member_id)},
        "amount": req.amount,
        "amount_words": AMOUNT_WORDS[req.amount],
        "note": req.note,
        "status": req.status,
        "resolution": req.resolution,
        "chip_in_cents": req.chip_in_cents,
        "answered_by": _member_chip(answered_by) if answered_by else None,
        "answered_at": req.answered_at.isoformat() if req.answered_at else None,
        "undo_until": req.undo_until.isoformat() if req.undo_until else None,
        "expires_at": req.expires_at.isoformat(),
        "created_at": req.created_at.isoformat(),
        "version": req.version,
    }


async def _lock_request(w: HouseWrite, request_id: uuid.UUID) -> tuple[ShareRequest, InventoryItem]:
    req = (
        await w.session.scalars(
            select(ShareRequest).where(ShareRequest.id == request_id).with_for_update(key_share=True)
        )
    ).first()
    if req is None or req.house_id != w.house_id:
        raise Problem(404, ErrorCode.NOT_FOUND, "That ask is gone")
    item = await w.session.get(InventoryItem, req.inventory_item_id)
    assert item is not None
    return req, item


async def request_house(db: AsyncSession, request_id: uuid.UUID) -> uuid.UUID:
    house_id = await db.scalar(select(ShareRequest.house_id).where(ShareRequest.id == request_id))
    if house_id is None:
        raise Problem(404, ErrorCode.NOT_FOUND, "That ask is gone")
    return house_id


async def _source_line(db: AsyncSession, item: InventoryItem) -> tuple[Bill | None, ItemRow | None]:
    if item.bill_id is None or item.item_id is None:
        return None, None
    return await db.get(Bill, item.bill_id), await db.get(ItemRow, item.item_id)


async def split_preview(db: AsyncSession, req: ShareRequest, item: InventoryItem) -> dict[str, int]:
    """FR-72: exact share deltas if the requester joins the line (compute_shares, nothing saved)."""
    bill, line = await _source_line(db, item)
    if bill is None or line is None or bill.state != "open":
        return {}
    rows = await bill_math.load(db, bill)
    before = bill_math.shares(rows).shares
    if not any(a.item_id == line.id and a.member_id == req.requester_member_id for a in rows.allocations):
        rows.allocations.append(
            Allocation(id=uuid7(), item_id=line.id, member_id=req.requester_member_id, weight=Decimal(1))
        )
    after = bill_math.shares(rows).shares
    return {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)}


async def chip_in_suggestion(db: AsyncSession, item: InventoryItem, owners: list[uuid.UUID]) -> int:
    """Line total / (owners + 1), largest-remainder (§19.4.3)."""
    _, line = await _source_line(db, item)
    total = line.line_total_cents if line else (item.price_cents or 0)
    if total <= 0:
        return 0
    keys = {str(o): 1 for o in owners} | {"asker": 1}
    order = {k: (0 if k == "asker" else 1, k) for k in keys}
    return largest_remainder(total, keys, order)["asker"]


async def answer(
    writes: WriteContext,
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    decision: str,
    chip_in_cents: int | None,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-72..FR-74: first owner to answer decides (row lock; ALREADY_ANSWERED for the second)."""
    async with writes.sessions() as db:
        house_id = await request_house(db, request_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        req, item = await _lock_request(w, request_id)
        owners = await _active_owner_ids(w.session, item.id)
        _require_owner(me, owners)
        if req.status != "pending" or req.expires_at <= now:
            if req.status == "pending":
                req.status = "expired"
            answered_by = await w.session.get(Member, req.answered_by_member_id) if req.answered_by_member_id else None
            if item.status != "in_stock" or req.status == "expired":
                raise Problem(410, ErrorCode.ITEM_GONE, "This ask has closed", extra={"status": req.status})
            raise Problem(
                409,
                ErrorCode.ALREADY_ANSWERED,
                f"Already answered by {answered_by.display_name if answered_by else 'someone'}",
                extra={"request": request_view(req, item, None, answered_by)},
            )
        bill, line = await _source_line(w.session, item)
        split_delta = 0
        if decision == "decline":
            req.status, req.resolution = "declined", None
        elif decision == "free":
            req.status, req.resolution = "approved", "free"
            w.session.add(
                InventoryGrant(
                    inventory_item_id=item.id,
                    member_id=req.requester_member_id,
                    share_request_id=req.id,
                    granted_by_member_id=me.member_id,
                    created_at=now,
                )
            )
        elif decision == "split":
            if bill is None or line is None:
                raise Problem(409, ErrorCode.CONFLICT, "Leftovers can only be shared on the house")
            if bill.state != "open":
                raise Problem(
                    409, ErrorCode.BILL_SETTLED, "That tab is settled now", extra={"options": ["free", "chip_in"]}
                )
            deltas = await _join_line(w, bill, line, req.requester_member_id, me.member_id, user_id, now)
            req.status, req.resolution = "approved", "split"
            split_delta = deltas.get(mkey(req.requester_member_id), 0)
        elif decision == "chip_in":
            if bill is None or line is None:
                raise Problem(409, ErrorCode.CONFLICT, "Leftovers can only be shared on the house")
            bill = (await w.session.scalars(select(Bill).where(Bill.id == bill.id).with_for_update(read=True))).one()
            if bill.state == "open":
                raise Problem(409, ErrorCode.CONFLICT, "The tab is still open: add them to the split instead")
            amount = chip_in_cents if chip_in_cents is not None else await chip_in_suggestion(w.session, item, owners)
            if not 1 <= amount <= line.line_total_cents:
                raise Problem(422, ErrorCode.VALIDATION_FAILED, "Chip in between $0.01 and the item's price")
            split = largest_remainder(amount, {str(o): 1 for o in owners}, {str(o): (0, str(o)) for o in owners})
            for owner, cents in split.items():
                if cents:
                    w.session.add(
                        LedgerEntry(
                            id=uuid7(),
                            house_id=house_id,
                            from_member_id=req.requester_member_id,
                            to_member_id=uuid.UUID(owner),
                            amount_cents=cents,
                            source="chip_in",
                            share_request_id=req.id,
                            created_at=now,
                        )
                    )
            w.session.add(
                InventoryGrant(
                    inventory_item_id=item.id,
                    member_id=req.requester_member_id,
                    share_request_id=req.id,
                    granted_by_member_id=me.member_id,
                    created_at=now,
                )
            )
            req.status, req.resolution, req.chip_in_cents = "approved", "chip_in", amount
        else:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "Pick an answer")
        req.answered_by_member_id = me.member_id
        req.answered_at = now
        req.undo_until = now + UNDO_WINDOW
        req.version += 1
        answered_by = await w.session.get(Member, me.member_id)
        w.emit(
            "request_answered",
            aggregate=("share_request", req.id, req.version),
            data={
                "id": str(req.id),
                "item_id": str(item.id),
                "requester": str(req.requester_member_id),
                "status": req.status,
                "resolution": req.resolution,
                "chip_in_cents": req.chip_in_cents,
                "split_delta_cents": split_delta,
            },
            activity=Activity(
                "ask_answered",
                bill_id=bill.id if decision == "split" and bill else None,
                actor_user_id=user_id,
                payload={"item": item.name, "decision": decision, "requester": str(req.requester_member_id)},
            ),
            notify=["answers"],
        )
        out = request_view(req, item, None, answered_by)
        w.respond(200, out)
    return out


async def _join_line(
    w: HouseWrite,
    bill: Bill,
    line: ItemRow,
    requester: uuid.UUID,
    approver: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> dict[str, int]:
    """FR-73: weight-1 allocation, shares recomputed with the invariant, snapshot, owners synced."""
    from app.services.bills import _after_money_change

    locked = (await w.session.scalars(select(Bill).where(Bill.id == bill.id).with_for_update(key_share=True))).one()
    if locked.state != "open":
        raise Problem(409, ErrorCode.BILL_SETTLED, "That tab is settled now", extra={"options": ["free", "chip_in"]})
    exists = await w.session.scalar(
        select(Allocation.id).where(Allocation.item_id == line.id, Allocation.member_id == requester)
    )
    if exists is None:
        w.session.add(
            Allocation(
                id=uuid7(),
                item_id=line.id,
                member_id=requester,
                weight=Decimal(1),
                created_by=user_id,
                created_at=now,
                updated_by_member_id=approver,
                updated_at=now,
            )
        )
        await w.session.flush()
    rows = await bill_math.load(w.session, locked)
    deltas = await _after_money_change(w, rows, "ask_split", now)
    names = {m.id: m.display_name for m in rows.members}
    w.emit(
        "allocation_changed",
        aggregate=("bill", locked.id, locked.version),
        data={
            "bill_id": str(locked.id),
            "changes": [{"item_id": str(line.id), "participant": mkey(requester), "on": True}],
            "shares": bill_math.shares(rows).shares,
            "share_deltas": deltas,
            "notify_skip": [mkey(requester)],  # the asker hears about it through the answer
        },
        notify=["share"] if deltas else [],
        activity=Activity(
            "allocation_changed",
            bill_id=locked.id,
            actor_user_id=user_id,
            payload={"text": f"{names[requester]} joined {short_name(line.name)}, approved by {names[approver]}"},
        ),
    )
    return deltas


async def undo_answer(
    writes: WriteContext, request_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    """FR-76: owners undo an answer within 5 minutes; undoing Add to split removes the allocation
    if the bill is still open; a chip-in is reversed with opposite ledger entries."""
    async with writes.sessions() as db:
        house_id = await request_house(db, request_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        req, item = await _lock_request(w, request_id)
        _require_owner(me, await _active_owner_ids(w.session, item.id))
        if req.status not in ("approved", "declined") or req.undo_until is None or now > req.undo_until:
            raise Problem(409, ErrorCode.CONFLICT, "Too late to undo")
        await w.session.execute(delete(InventoryGrant).where(InventoryGrant.share_request_id == req.id))
        if req.resolution == "split":
            bill, line = await _source_line(w.session, item)
            if bill is not None:  # same lock as a settle or an edit: the state can't change under us
                bill = (
                    await w.session.scalars(select(Bill).where(Bill.id == bill.id).with_for_update(key_share=True))
                ).one()
            if bill is None or line is None or bill.state != "open":
                raise Problem(409, ErrorCode.BILL_SETTLED, "That tab is settled now; the split stays")
            from app.services.bills import _after_money_change

            # Remove only the allocation the split created (written at the answer time); a tick the
            # payer made for them before or after stays.
            await w.session.execute(
                delete(Allocation).where(
                    Allocation.item_id == line.id,
                    Allocation.member_id == req.requester_member_id,
                    Allocation.created_at == req.answered_at,
                )
            )
            await w.session.flush()
            await w.lock_house()  # shares from every committed tick (ADR-0023)
            rows = await bill_math.load(w.session, bill)
            deltas = await _after_money_change(w, rows, "ask_undo", now)
            w.emit(
                "allocation_changed",
                aggregate=("bill", bill.id, bill.version),
                data={
                    "bill_id": str(bill.id),
                    "changes": [{"item_id": str(line.id), "participant": mkey(req.requester_member_id), "on": False}],
                    "shares": bill_math.shares(rows).shares,
                    "share_deltas": deltas,
                },
                notify=["share"] if deltas else [],
            )
        if req.resolution == "chip_in":
            for e in (await w.session.scalars(select(LedgerEntry).where(LedgerEntry.share_request_id == req.id))).all():
                w.session.add(
                    LedgerEntry(
                        id=uuid7(),
                        house_id=house_id,
                        from_member_id=e.to_member_id,
                        to_member_id=e.from_member_id,
                        amount_cents=e.amount_cents,
                        source="chip_in",
                        share_request_id=req.id,
                        created_at=now,
                    )
                )
        req.status, req.resolution, req.chip_in_cents = "pending", None, None
        req.answered_by_member_id = req.answered_at = req.undo_until = None
        req.version += 1
        w.emit(
            "request_answered",
            aggregate=("share_request", req.id, req.version),
            data={"id": str(req.id), "status": "pending"},
            activity=Activity("ask_undone", payload={"item": item.name}, actor_user_id=user_id),
        )
        out = request_view(req, item, None, None)
        w.respond(200, out)
    return out


async def cancel(
    writes: WriteContext, request_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    async with writes.sessions() as db:
        house_id = await request_house(db, request_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        req, item = await _lock_request(w, request_id)
        require(Action.ASK_CANCEL, actor=me, ask=AskCtx(req.requester_member_id))
        if req.status != "pending":
            raise Problem(409, ErrorCode.ALREADY_ANSWERED, "This ask was already answered")
        req.status = "cancelled"
        req.version += 1
        w.emit(
            "request_answered",
            aggregate=("share_request", req.id, req.version),
            data={"id": str(req.id), "status": "cancelled"},
        )
        out = request_view(req, item, None, None)
        w.respond(200, out)
    return out


async def expire_requests(writes: WriteContext, now: datetime) -> int:
    """Daily sweeper (FR-76): pending asks past 48 h become expired, one house write per house with a
    `requests_expired` event so every device (the asker included) hears about it. Reads never write:
    `effective_status` already shows such asks as expired until the sweep runs."""
    async with writes.sessions() as db:
        houses = (
            await db.scalars(
                select(ShareRequest.house_id)
                .where(ShareRequest.status == "pending", ShareRequest.expires_at <= now)
                .distinct()
            )
        ).all()
    total = 0
    for house_id in houses:
        async with house_write(writes, house_id) as w:
            rows = (
                await w.session.scalars(
                    select(ShareRequest)
                    .where(
                        ShareRequest.house_id == house_id,
                        ShareRequest.status == "pending",
                        ShareRequest.expires_at <= now,
                    )
                    .order_by(ShareRequest.id)
                    .with_for_update(key_share=True)
                )
            ).all()
            for r in rows:
                r.status = "expired"
                r.version += 1
            if rows:
                w.emit(
                    "requests_expired",
                    aggregate=("house", house_id, None),
                    data={"ids": [str(r.id) for r in rows]},
                    activity=Activity("requests_expired", payload={"count": len(rows)}),
                )
            total += len(rows)
    return total


def effective_status(req: ShareRequest, now: datetime) -> str:
    """A pending ask past its 48 h reads as expired even before the daily sweep has run."""
    return "expired" if req.status == "pending" and req.expires_at <= now else req.status


async def member_left(db: AsyncSession, member_id: uuid.UUID) -> None:
    """EDGE-19-09: a departing member's pending asks are cancelled. EDGE-19-02 needs no write: views
    count only active members as owners, so an item whose owners all left is open to everyone."""
    await db.execute(
        update(ShareRequest)
        .where(ShareRequest.requester_member_id == member_id, ShareRequest.status == "pending")
        .values(status="cancelled")
    )
