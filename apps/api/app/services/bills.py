"""Bills: drafts, items, allocations, reconciliation, send (PRD §5.2-§5.4, §6, §21.3; FR-13..FR-31).

Write kinds (PRD §21.3):
* set-state  PUT  allocations, exclusions, item order   (no If-Match; last write wins per cell)
* guarded    PATCH bill header, item                     (If-Match: version; 409 VERSION_CONFLICT)
* transition POST send, override, delete-draft           (Idempotency-Key; row lock; state machine)
* create     POST bill, item, guest, fill-rest, merge, split (Idempotency-Key + X-Client-Mutation-Id)

Every write runs in `house_write`: rows locked first, house sequence last, event + activity +
outbox in the same commit. Ticks never bump the bill or item version (SYNC-04).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.kitchen import storage_for
from app.domain.money import InvariantError, MoneyError
from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.links import bill_token, derive_token
from app.models import Allocation, Bill, BillExclusion, BillFee, Guest, House, Member, ShareSnapshot
from app.models import Item as ItemRow
from app.models.enums import MAX_BILL_TOTAL_CENTS, MAX_ITEMS_PER_BILL
from app.policy import Action, BillCtx, MemberCtx, require
from app.services import bill_math, kitchen
from app.services.bill_math import BillRows, gkey, mkey
from app.services.house_write import Activity, HouseWrite, Idempotency, WriteContext, house_write
from app.services.houses import membership
from app.tokens import hash_token

# ---- helpers -------------------------------------------------------------------------------------


def bill_ctx(bill: Bill) -> BillCtx:
    return BillCtx(bill.payer_member_id, bill.created_by, bill.state)


async def bill_house_id(db: AsyncSession, bill_id: uuid.UUID) -> uuid.UUID:
    house_id = await db.scalar(select(Bill.house_id).where(Bill.id == bill_id, Bill.deleted_at.is_(None)))
    if house_id is None:
        raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
    return house_id


async def _lock_bill(w: HouseWrite, bill_id: uuid.UUID) -> Bill:
    bill = (await w.session.scalars(select(Bill).where(Bill.id == bill_id).with_for_update(key_share=True))).first()
    if bill is None or bill.deleted_at is not None:
        raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
    return bill


async def _actor(w: HouseWrite, user_id: uuid.UUID) -> MemberCtx:
    me = await membership(w.session, w.house_id, user_id)
    if me is None or not me.active:
        raise Problem(403, ErrorCode.NOT_A_MEMBER, "You're not in this house")
    w.actor_member_id = me.member_id
    return me


def _today(house: House | None, now: datetime) -> date:
    tz = ZoneInfo(house.timezone) if house else ZoneInfo("America/New_York")
    return now.astimezone(tz).date()


def _version_check(row: Any, expected: int, view: dict[str, Any]) -> None:
    if row.version != expected:
        raise Problem(409, ErrorCode.VERSION_CONFLICT, "Someone just changed this", extra={"current": view})


async def _refresh_totals(db: AsyncSession, rows: BillRows, now: datetime) -> None:
    """Keep subtotal/total consistent with items and fees; keep an FR-13 override pinned to the
    printed total by resizing the signed adjustment (ADR-0002)."""
    bill = rows.bill
    existing = next((f for f in rows.fees if f.kind == "adjustment"), None)
    amount = 0
    if bill.printed_total_cents is not None:
        amount = bill.printed_total_cents - bill_math.computed_total(rows, include_adjustment=False)
    if existing is not None and amount == 0:
        await db.delete(existing)
        rows.fees.remove(existing)
    elif existing is not None:
        existing.amount_cents = amount
    elif amount != 0:
        fee = BillFee(
            id=uuid7(),
            bill_id=bill.id,
            kind="adjustment",
            label="Receipt adjustment",
            amount_cents=amount,
            position=len(rows.fees),
            created_at=now,
        )
        db.add(fee)
        rows.fees.append(fee)
    bill.subtotal_cents = sum(i.line_total_cents for i in rows.items)
    total = bill_math.computed_total(rows)
    if not 0 <= total <= MAX_BILL_TOTAL_CENTS:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "A tab's total must be between $0.00 and $100,000.00")
    bill.total_cents = total
    bill.updated_at = now


async def _after_money_change(
    w: HouseWrite, rows: BillRows, reason: str, now: datetime, *, owners: bool = True
) -> dict[str, int]:
    """Open bills: recompute shares under the lock, snapshot the changed ones, keep kitchen owners in
    sync (FR-62). Drafts: nothing to snapshot. Refuses to save if the invariant fails (§6 step 5)."""
    if rows.bill.state != "open":
        return {}
    try:
        result = bill_math.shares(rows)
    except InvariantError as exc:  # pragma: no cover - guarded by the property tests
        raise Problem(500, ErrorCode.INTERNAL, "Something jammed. Try again.") from exc
    except MoneyError as exc:
        raise Problem(409, ErrorCode.LAST_PERSON, "Every item needs someone on it") from exc
    previous, last_at = await snapshot_state(w.session, rows.bill.id)
    # Callers hold the bill's exclusive lock or the house lock, so snapshots are written one at a
    # time: stamp them strictly after the previous one so "latest" means latest committed even
    # when an earlier-started request commits later or the clock is pinned (ADR-0023).
    stamp = max(now, last_at + timedelta(microseconds=1)) if last_at is not None else now
    changed = {k: v for k, v in result.shares.items() if previous.get(k, 0) != v}
    if changed:  # one prepared statement (this runs under the lock on the hot tick path)
        keys = list(changed)
        await w.session.execute(
            text(
                "INSERT INTO share_snapshots (id, bill_id, member_id, guest_id, share_cents, bill_version, "
                "computed_at, reason) "
                "SELECT s.id, :bill, s.member_id, s.guest_id, s.cents, :version, :at, :reason "
                "FROM unnest(CAST(:ids AS uuid[]), CAST(:members AS uuid[]), CAST(:guests AS uuid[]), "
                "CAST(:cents AS bigint[])) AS s(id, member_id, guest_id, cents)"
            ),
            {
                "bill": rows.bill.id,
                "version": rows.bill.version,
                "at": stamp,
                "reason": reason,
                "ids": [uuid7() for _ in keys],
                "members": [uuid.UUID(k[2:]) if k.startswith("m:") else None for k in keys],
                "guests": [uuid.UUID(k[2:]) if k.startswith("g:") else None for k in keys],
                "cents": [changed[k] for k in keys],
            },
        )
    if owners:
        await kitchen.sync_owners(w.session, rows, now)
    return {k: v - previous.get(k, 0) for k, v in changed.items()}


async def latest_snapshot(db: AsyncSession, bill_id: uuid.UUID) -> dict[str, int]:
    return (await snapshot_state(db, bill_id))[0]


async def snapshot_state(db: AsyncSession, bill_id: uuid.UUID) -> tuple[dict[str, int], datetime | None]:
    """Each participant's latest stored share, and when the latest snapshot was written. One row per
    participant (DISTINCT ON over the (bill_id, computed_at) index), so the cost doesn't grow with a
    bill's history of ticks."""
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT ON (member_id, guest_id) member_id, guest_id, share_cents, computed_at "
                "FROM share_snapshots WHERE bill_id = :bill "
                "ORDER BY member_id, guest_id, computed_at DESC, id DESC"
            ),
            {"bill": bill_id},
        )
    ).all()
    out: dict[str, int] = {}
    last_at: datetime | None = None
    for member_id, guest_id, cents, at in rows:
        key = mkey(member_id) if member_id is not None else gkey(guest_id) if guest_id is not None else None
        assert key is not None  # exactly one is set (CHECK)
        out[key] = cents
        last_at = at if last_at is None or at > last_at else last_at
    return out, last_at


# ---- views --------------------------------------------------------------------------------------


def item_view(i: ItemRow, allocations: list[Allocation]) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "position": i.position,
        "name": i.name,
        "raw_text": i.raw_text,
        "quantity": str(i.quantity),
        "unit_price_cents": i.unit_price_cents,
        "discount_cents": i.discount_cents,
        "line_total_cents": i.line_total_cents,
        "taxable": i.taxable,
        "confidence": None if i.confidence is None else str(i.confidence),
        "flagged": i.flagged,
        "storage": i.storage,
        "category": i.category,
        "icon_key": i.icon_key,
        "shelf_life_days": i.shelf_life_days,
        "version": i.version,
        "allocations": [
            {
                "participant": mkey(a.member_id) if a.member_id else gkey(a.guest_id),  # type: ignore[arg-type]
                "weight": str(a.weight),
                "share_num": a.share_num,
                "share_den": a.share_den,
            }
            for a in allocations
        ],
    }


def participants_view(rows: BillRows, allocated: set[str]) -> list[dict[str, Any]]:
    out = []
    for m in rows.members:
        key = mkey(m.id)
        if m.status not in ("active", "placeholder") and key not in allocated:
            continue  # removed/left members show only on bills they're on (EDGE-15-13)
        out.append(
            {
                "key": key,
                "kind": "member",
                "id": str(m.id),
                "display_name": m.display_name,
                "initial": m.display_name[:1].upper(),
                "color": m.color,
                "sort_order": m.sort_order,
                "status": m.status,
                "excluded": m.id in rows.excluded,
                "claimed": m.user_id is not None,
            }
        )
    base = max((m.sort_order for m in rows.members), default=-1) + 1
    for g in rows.guests:
        out.append(
            {
                "key": gkey(g.id),
                "kind": "guest",
                "id": str(g.id),
                "display_name": g.display_name,
                "initial": g.display_name[:1].upper(),
                "color": "#C9C6BB",
                "sort_order": base + g.sort_order,
                "status": "guest",
                "excluded": False,
                "claimed": g.user_id is not None,
            }
        )
    return out


async def duplicate_of(db: AsyncSession, bill: Bill) -> dict[str, Any] | None:
    """FR-17: same store, date and total within $0.01 in this house in the last 14 days."""
    if not bill.store_name or bill.total_cents == 0:
        return None
    other = (
        await db.scalars(
            select(Bill)
            .where(
                Bill.house_id == bill.house_id,
                Bill.id != bill.id,
                Bill.deleted_at.is_(None),
                Bill.state != "voided",
                func.lower(Bill.store_name) == bill.store_name.lower(),
                Bill.purchase_date == bill.purchase_date,
                func.abs(Bill.total_cents - bill.total_cents) <= 1,
                Bill.created_at >= bill.created_at - timedelta(days=14),
            )
            .order_by(Bill.created_at)
        )
    ).first()
    if other is None:
        return None
    return {
        "id": str(other.id),
        "store_name": other.store_name,
        "purchase_date": other.purchase_date.isoformat(),
        "state": other.state,
    }


async def _scanned_offer(db: AsyncSession, bill: Bill) -> dict[str, Any] | None:
    from app.services.receipts import scanned_offer  # receipts builds on this module

    return await scanned_offer(db, bill.id)


LONG_OPEN = timedelta(days=60)  # PRD §15: after 60 days, a gentle banner to the payer; no auto-settle


async def bill_view(
    db: AsyncSession, pepper: str, bill: Bill, me: MemberCtx | None, now: datetime | None = None
) -> dict[str, Any]:
    rows = await bill_math.load(db, bill)
    by_item = rows.allocations_by_item()
    allocated = {mkey(a.member_id) if a.member_id else gkey(a.guest_id) for a in rows.allocations}  # type: ignore[arg-type]
    final = not rows.unassigned and bool(rows.items)
    try:
        result = bill_math.shares(rows) if final and bill_math.reconciliation(rows)["ok"] else bill_math.preview(rows)
        share_map = result.shares
    except MoneyError:
        share_map = bill_math.preview(rows).shares
        final = False
    is_payer = me is not None and me.member_id == bill.payer_member_id
    can_edit = (
        me is not None
        and bill.state in ("draft", "open")
        and (is_payer or (bill.state == "draft" and me.user_id == bill.created_by))
    )
    return {
        "bill": {
            "id": str(bill.id),
            "house_id": str(bill.house_id),
            "store_name": bill.store_name,
            "purchase_date": bill.purchase_date.isoformat(),
            "currency": bill.currency,
            "state": bill.state,
            "version": bill.version,
            "payer_member_id": str(bill.payer_member_id),
            "subtotal_cents": bill.subtotal_cents,
            "tax_cents": bill.tax_cents,
            "total_cents": bill.total_cents,
            "receipt_total_cents": bill.receipt_total_cents,
            "extraction_status": bill.extraction_status,
            "sent_at": bill.sent_at.isoformat() if bill.sent_at else None,
            "settled_at": bill.settled_at.isoformat() if bill.settled_at else None,
            "void_reason": bill.void_reason,
            "link_token": bill_token(pepper, bill.id, bill.created_at)[0] if bill.state != "draft" else None,
        },
        "items": [item_view(i, by_item[i.id]) for i in rows.items],
        "fees": [
            {"id": str(f.id), "kind": f.kind, "label": f.label, "amount_cents": f.amount_cents} for f in rows.fees
        ],
        "participants": participants_view(rows, allocated),
        "shares": share_map,
        "shares_final": final,
        "progress": {"assigned": len(rows.items) - len(rows.unassigned), "total": len(rows.items)},
        "reconciliation": bill_math.reconciliation(rows),
        "duplicate_of": await duplicate_of(db, bill) if bill.state == "draft" else None,
        # "Use the scanned items instead?" (PRD §21.6, SYNC-15): a scan kept back because of hand edits
        "scanned_offer": await _scanned_offer(db, bill) if bill.state == "draft" else None,
        "me": {"member_id": str(me.member_id) if me else None, "is_payer": is_payer, "can_edit": can_edit},
        # EDGE-15-25: only the payer sees it; the tab stays open until they settle it
        "long_open": bool(
            is_payer and bill.state == "open" and bill.sent_at and now and now - bill.sent_at >= LONG_OPEN
        ),
    }


# ---- create / header / delete ----------------------------------------------------------------------


async def create_bill(
    writes: WriteContext,
    house_id: uuid.UUID,
    user_id: uuid.UUID,
    body: dict[str, Any],
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        require(Action.BILL_CREATE, actor=me)
        house = await w.session.get(House, house_id)
        payer = uuid.UUID(body["payer_member_id"]) if body.get("payer_member_id") else me.member_id
        await _ensure_active_member(w.session, house_id, payer)
        bill = Bill(
            id=uuid7(),
            house_id=house_id,
            payer_member_id=payer,
            created_by=user_id,
            store_name=(body.get("store_name") or None),
            purchase_date=body.get("purchase_date") or _today(house, now),
            receipt_total_cents=body.get("receipt_total_cents"),
            created_at=now,
            updated_at=now,
        )
        w.session.add(bill)
        await w.session.flush()
        w.emit(
            "bill_created",
            aggregate=("bill", bill.id, 1),
            data={"bill_id": str(bill.id)},
            activity=Activity("bill_created", bill_id=bill.id, actor_user_id=user_id),
        )
        body_out = {"id": str(bill.id), "version": 1, "state": "draft"}
        w.respond(201, body_out)
    return body_out


async def _ensure_active_member(db: AsyncSession, house_id: uuid.UUID, member_id: uuid.UUID) -> Member:
    m = await db.get(Member, member_id)
    if m is None or m.house_id != house_id or m.status not in ("active", "placeholder"):
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Paid by must be someone in the house")
    return m


async def update_header(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    body: dict[str, Any],
    expected: int,
    now: datetime,
    cmid: str | None,
) -> dict[str, Any]:
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        _version_check(
            bill,
            expected,
            {
                "version": bill.version,
                "store_name": bill.store_name,
                "tax_cents": bill.tax_cents,
                "receipt_total_cents": bill.receipt_total_cents,
            },
        )
        rows = await bill_math.load(w.session, bill)
        if "store_name" in body:
            bill.store_name = (body["store_name"] or "").strip()[:80] or None
        if body.get("purchase_date"):
            bill.purchase_date = body["purchase_date"]
        if body.get("payer_member_id"):
            await _ensure_active_member(w.session, house_id, uuid.UUID(body["payer_member_id"]))
            bill.payer_member_id = uuid.UUID(body["payer_member_id"])
        if "tax_cents" in body and body["tax_cents"] is not None:
            bill.tax_cents = int(body["tax_cents"])
        if "receipt_total_cents" in body:
            if body["receipt_total_cents"] != bill.receipt_total_cents:
                # A new printed total clears any override; the payer reconciles again.
                bill.printed_total_cents = None
                bill.reconcile_override_reason = None
            bill.receipt_total_cents = body["receipt_total_cents"]
        if body.get("fees") is not None:
            for f in [f for f in rows.fees if f.kind != "adjustment"]:
                await w.session.delete(f)
                rows.fees.remove(f)
            for pos, f in enumerate(body["fees"]):
                fee = BillFee(
                    id=uuid7(),
                    bill_id=bill.id,
                    kind=f["kind"],
                    label=f["label"][:40],
                    amount_cents=int(f["amount_cents"]),
                    position=pos,
                    created_at=now,
                )
                w.session.add(fee)
                rows.fees.append(fee)
        await w.session.flush()
        await _refresh_totals(w.session, rows, now)
        bill.version += 1
        deltas = await _after_money_change(w, rows, "header_edit", now)
        w.emit(
            "bill_updated",
            aggregate=("bill", bill.id, bill.version),
            data={"bill_id": str(bill.id), "fields": sorted(body), "share_deltas": deltas},
            activity=Activity("bill_edited", bill_id=bill.id, actor_user_id=user_id, payload={"fields": sorted(body)}),
            notify=["share"] if deltas else [],
        )
        return {"id": str(bill.id), "version": bill.version, "total_cents": bill.total_cents}


async def override_reconciliation(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    reason: str,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-13 "Use receipt total anyway": the printed total wins via a signed adjustment (ADR-0002)."""
    reason = " ".join(reason.split())
    if not 3 <= len(reason) <= 200:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Say why in 3 to 200 characters")
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        if bill.receipt_total_cents is None:
            raise Problem(409, ErrorCode.CONFLICT, "Enter the receipt total first")
        rows = await bill_math.load(w.session, bill)
        difference = bill.receipt_total_cents - bill_math.computed_total(rows, include_adjustment=False)
        bill.printed_total_cents = bill.receipt_total_cents
        bill.reconcile_override_reason = reason
        await _refresh_totals(w.session, rows, now)
        bill.version += 1
        deltas = await _after_money_change(w, rows, "reconcile_override", now)
        w.emit(
            "bill_updated",
            aggregate=("bill", bill.id, bill.version),
            data={"bill_id": str(bill.id), "override": True, "share_deltas": deltas},
            notify=["share"] if deltas else [],
            activity=Activity(
                "reconcile_override",
                bill_id=bill.id,
                actor_user_id=user_id,
                payload={"reason": reason, "difference_cents": difference},
            ),
        )
        out = {
            "id": str(bill.id),
            "version": bill.version,
            "total_cents": bill.total_cents,
            "adjustment_cents": difference,
        }
        w.respond(200, out)
    return out


async def delete_draft(
    writes: WriteContext, bill_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_DELETE_DRAFT, actor=me, bill=bill_ctx(bill))
        bill.deleted_at = now
        bill.version += 1
        w.emit(
            "bill_deleted",
            aggregate=("bill", bill.id, bill.version),
            activity=Activity("bill_deleted", bill_id=bill.id, actor_user_id=user_id),
        )
        out = {"id": str(bill.id), "deleted": True}
        w.respond(200, out)
    return out


# ---- items ----------------------------------------------------------------------------------------


def _item_fields(body: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in body:
        name = " ".join(str(body["name"]).split())
        if not 1 <= len(name) <= 120:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "Item names are 1 to 120 characters")
        out["name"] = name
    for key in ("line_total_cents", "unit_price_cents", "discount_cents", "shelf_life_days"):
        if key in body:
            out[key] = body[key]
    for key in ("quantity", "taxable", "category", "icon_key", "raw_text"):
        if key in body:
            out[key] = body[key]
    if "storage" in body or "category" in body:
        out["storage"] = storage_for(body.get("category"), body.get("storage", "pantry"))
    if "line_total_cents" in out and abs(out["line_total_cents"]) > MAX_BILL_TOTAL_CENTS:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "That price is too large")
    return out


async def _item_count(db: AsyncSession, bill_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(ItemRow).where(ItemRow.bill_id == bill_id, ItemRow.deleted_at.is_(None))
        )
        or 0
    )


async def add_item(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    body: dict[str, Any],
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    fields = _item_fields(body)
    if "name" not in fields or "line_total_cents" not in fields:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "An item needs a name and a price")
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        count = await _item_count(w.session, bill_id)
        if count >= MAX_ITEMS_PER_BILL:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, f"A tab can have at most {MAX_ITEMS_PER_BILL} items")
        fields.setdefault("storage", "pantry")
        item = ItemRow(
            id=uuid7(),
            bill_id=bill_id,
            position=count,
            created_at=now,
            updated_at=now,
            unit_price_cents=fields.pop("unit_price_cents", fields["line_total_cents"]),
            **fields,
        )
        w.session.add(item)
        await w.session.flush()
        rows = await bill_math.load(w.session, bill)
        await _refresh_totals(w.session, rows, now)
        if bill.state == "open":
            await kitchen.stock_item(w.session, rows, item, now)  # FR-62: added lines are stocked
        w.emit(
            "item_added",
            aggregate=("item", item.id, 1),
            data=item_view(item, []),
            activity=Activity(
                "item_added",
                bill_id=bill_id,
                actor_user_id=user_id,
                payload={"name": item.name, "cents": item.line_total_cents},
            ),
        )
        out = item_view(item, [])
        w.respond(201, out)
    return out


async def update_item(
    writes: WriteContext,
    bill_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    body: dict[str, Any],
    expected: int,
    now: datetime,
    cmid: str | None,
) -> dict[str, Any]:
    fields = _item_fields(body)
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        item = (
            await w.session.scalars(
                select(ItemRow).where(ItemRow.id == item_id, ItemRow.bill_id == bill_id).with_for_update(key_share=True)
            )
        ).first()
        if item is None or item.deleted_at is not None:
            raise Problem(404, ErrorCode.ITEM_GONE, "That item was removed")
        allocations = list((await w.session.scalars(select(Allocation).where(Allocation.item_id == item_id))).all())
        _version_check(item, expected, item_view(item, allocations))
        for key, value in fields.items():
            setattr(item, key, value)
        item.version += 1
        item.updated_at = now
        await w.session.flush()
        rows = await bill_math.load(w.session, bill)
        await _refresh_totals(w.session, rows, now)
        deltas = await _after_money_change(w, rows, "item_edit", now)
        if bill.state == "open":
            await kitchen.restock_item(w.session, rows, item, now)  # retag / rename keeps the kitchen in sync
        w.emit(
            "item_updated",
            aggregate=("item", item.id, item.version),
            data={**item_view(item, allocations), "bill_id": str(bill_id), "share_deltas": deltas},
            activity=Activity(
                "item_edited",
                bill_id=bill_id,
                actor_user_id=user_id,
                payload={"name": item.name, "fields": sorted(fields)},
            ),
            notify=["share"] if deltas else [],
        )
        return item_view(item, allocations)


async def delete_item(
    writes: WriteContext, bill_id: uuid.UUID, item_id: uuid.UUID, user_id: uuid.UUID, now: datetime, cmid: str | None
) -> dict[str, Any]:
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        item = (
            await w.session.scalars(
                select(ItemRow).where(ItemRow.id == item_id, ItemRow.bill_id == bill_id).with_for_update(key_share=True)
            )
        ).first()
        if item is None or item.deleted_at is not None:
            return {"id": str(item_id), "deleted": True}  # already gone: no-op, no event
        item.deleted_at = now
        item.version += 1
        await w.session.flush()
        rows = await bill_math.load(w.session, bill)
        await _refresh_totals(w.session, rows, now)
        deltas = await _after_money_change(w, rows, "item_deleted", now)
        if bill.state == "open":
            await kitchen.unstock_item(w.session, item, now)  # FR-62 / EDGE-19-06
        await _renumber(w.session, rows)
        w.emit(
            "item_deleted",
            aggregate=("item", item.id, item.version),
            data={"item_id": str(item.id), "bill_id": str(bill_id), "share_deltas": deltas},
            activity=Activity("item_deleted", bill_id=bill_id, actor_user_id=user_id, payload={"name": item.name}),
            notify=["share"] if deltas else [],
        )
        return {"id": str(item_id), "deleted": True}


async def _renumber(db: AsyncSession, rows: BillRows) -> None:
    for pos, item in enumerate(rows.items):
        item.position = pos


async def reorder_items(
    writes: WriteContext, bill_id: uuid.UUID, user_id: uuid.UUID, item_ids: list[uuid.UUID], cmid: str | None
) -> dict[str, Any]:
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        rows = await bill_math.load(w.session, bill)
        current = [i.id for i in rows.items]
        if sorted(current) != sorted(item_ids):
            raise Problem(409, ErrorCode.CONFLICT, "The item list changed; refresh and try again")
        if current == item_ids:
            return {"order": [str(i) for i in item_ids]}
        lookup = {i.id: i for i in rows.items}
        for pos, iid in enumerate(item_ids):
            lookup[iid].position = pos
        w.emit("items_reordered", aggregate=("bill", bill.id, bill.version), data={"order": [str(i) for i in item_ids]})
        return {"order": [str(i) for i in item_ids]}


async def merge_items(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    item_ids: list[uuid.UUID],
    name: str | None,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    if len(set(item_ids)) < 2:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Pick at least two items to merge")
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        if bill.state != "draft":
            raise Problem(409, ErrorCode.CONFLICT, "Merge items before sending")
        rows = await bill_math.load(w.session, bill)
        picked = [i for i in rows.items if i.id in set(item_ids)]
        if len(picked) != len(set(item_ids)):
            raise Problem(404, ErrorCode.ITEM_GONE, "An item was removed")
        by_item = rows.allocations_by_item()
        first = picked[0]
        merged = ItemRow(
            id=uuid7(),
            bill_id=bill_id,
            position=first.position,
            name=_item_fields({"name": name or first.name})["name"],
            raw_text=" + ".join(i.raw_text or i.name for i in picked)[:500],
            line_total_cents=sum(i.line_total_cents for i in picked),
            unit_price_cents=sum(i.line_total_cents for i in picked),
            storage=first.storage,
            category=first.category,
            icon_key=first.icon_key,
            shelf_life_days=first.shelf_life_days,
            created_at=now,
            updated_at=now,
        )
        w.session.add(merged)
        await w.session.flush()
        keys: set[tuple[uuid.UUID | None, uuid.UUID | None]] = set()
        for i in picked:
            i.deleted_at = now
            for a in by_item[i.id]:
                keys.add((a.member_id, a.guest_id))
        for member_id, guest_id in keys:
            w.session.add(
                Allocation(
                    id=uuid7(),
                    item_id=merged.id,
                    member_id=member_id,
                    guest_id=guest_id,
                    created_by=user_id,
                    created_at=now,
                    updated_by_member_id=me.member_id,
                    updated_at=now,
                )
            )
        await w.session.flush()
        rows = await bill_math.load(w.session, bill)
        await _renumber(w.session, rows)
        await _refresh_totals(w.session, rows, now)
        w.emit(
            "items_merged",
            aggregate=("item", merged.id, 1),
            data={"merged": [str(i.id) for i in picked], "into": str(merged.id)},
            activity=Activity("items_merged", bill_id=bill_id, actor_user_id=user_id, payload={"name": merged.name}),
        )
        out = {"id": str(merged.id), "line_total_cents": merged.line_total_cents}
        w.respond(201, out)
    return out


async def split_item(
    writes: WriteContext,
    bill_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    parts: list[dict[str, Any]],
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    if len(parts) < 2:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Split into at least two lines")
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        if bill.state != "draft":
            raise Problem(409, ErrorCode.CONFLICT, "Split items before sending")
        rows = await bill_math.load(w.session, bill)
        item = next((i for i in rows.items if i.id == item_id), None)
        if item is None:
            raise Problem(404, ErrorCode.ITEM_GONE, "That item was removed")
        if sum(int(p["line_total_cents"]) for p in parts) != item.line_total_cents:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "The parts must add up to the original price")
        allocations = rows.allocations_by_item()[item.id]
        item.deleted_at = now
        for later in rows.items:  # make room so the parts sit where the original was
            if later.position > item.position:
                later.position += len(parts) - 1
        new_ids = []
        for n, part in enumerate(parts):
            new = ItemRow(
                id=uuid7(),
                bill_id=bill_id,
                position=item.position + n,
                name=_item_fields({"name": part.get("name") or item.name})["name"],
                raw_text=item.raw_text,
                line_total_cents=int(part["line_total_cents"]),
                unit_price_cents=int(part["line_total_cents"]),
                storage=item.storage,
                category=item.category,
                icon_key=item.icon_key,
                shelf_life_days=item.shelf_life_days,
                created_at=now,
                updated_at=now,
            )
            w.session.add(new)
            await w.session.flush()
            new_ids.append(new.id)
            for a in allocations:
                w.session.add(
                    Allocation(
                        id=uuid7(),
                        item_id=new.id,
                        member_id=a.member_id,
                        guest_id=a.guest_id,
                        weight=a.weight,
                        share_num=a.share_num,
                        share_den=a.share_den,
                        created_by=user_id,
                        created_at=now,
                        updated_by_member_id=me.member_id,
                        updated_at=now,
                    )
                )
        await w.session.flush()
        rows = await bill_math.load(w.session, bill)
        await _renumber(w.session, rows)
        await _refresh_totals(w.session, rows, now)
        w.emit(
            "item_split",
            aggregate=("item", item.id, item.version),
            data={"into": [str(i) for i in new_ids]},
            activity=Activity("item_split", bill_id=bill_id, actor_user_id=user_id, payload={"name": item.name}),
        )
        out = {"ids": [str(i) for i in new_ids]}
        w.respond(201, out)
    return out


# ---- allocations (set-state) ------------------------------------------------------------------------


def _parse_participant(raw: str) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    kind, _, value = raw.partition(":")
    try:
        ident = uuid.UUID(value)
    except ValueError as exc:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Unknown person") from exc
    if kind == "m":
        return ident, None
    if kind == "g":
        return None, ident
    raise Problem(422, ErrorCode.VALIDATION_FAILED, "Unknown person")


async def set_allocations(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    changes: list[dict[str, Any]],
    now: datetime,
    cmid: str | None,
) -> dict[str, Any]:
    """PUT the desired state of (item, participant) cells. Order-independent; a cell already in the
    desired state is a no-op (SYNC-02). Unticking the last person on an open bill's item fails with
    LAST_PERSON under the item lock (SYNC-03). Ticks don't bump versions (SYNC-04)."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _actor(w, user_id)
        bill = await _bill_shared_lock(w, bill_id)
        require(Action.BILL_READ, actor=me, bill=bill_ctx(bill))
        if bill.state in ("settled", "voided"):
            require(Action.ALLOC_SET, actor=me, bill=bill_ctx(bill))  # raises BILL_SETTLED / BILL_VOIDED
        rows = await bill_math.load(w.session, bill)
        items = {i.id: i for i in rows.items}
        members = {m.id: m for m in rows.members}
        guests = {g.id: g for g in rows.guests}
        applied = []
        # Lock touched items in a stable order so concurrent batches can't deadlock.
        touched = sorted({uuid.UUID(str(c["item_id"])) for c in changes})
        locked = {
            i.id
            for i in (
                await w.session.scalars(
                    select(ItemRow)
                    .where(ItemRow.id.in_(touched), ItemRow.deleted_at.is_(None))
                    .order_by(ItemRow.id)
                    .with_for_update(key_share=True)
                )
            ).all()
        }
        for change in changes:
            item_id = uuid.UUID(str(change["item_id"]))
            if item_id not in locked or item_id not in items:
                raise Problem(404, ErrorCode.ITEM_GONE, "That item was removed", extra={"item_id": str(item_id)})
            member_id, guest_id = _parse_participant(str(change["participant"]))
            require(Action.ALLOC_SET, actor=me, bill=bill_ctx(bill), participant_member_id=member_id)
            if member_id is not None:
                m = members.get(member_id)
                if m is None or (change["on"] and (m.status not in ("active", "placeholder") or m.id in rows.excluded)):
                    raise Problem(422, ErrorCode.VALIDATION_FAILED, "They can't be added to this tab")
            elif guest_id not in guests:
                raise Problem(422, ErrorCode.VALIDATION_FAILED, "Unknown guest")
            q = select(Allocation).where(Allocation.item_id == item_id)
            q = q.where(Allocation.member_id == member_id) if member_id else q.where(Allocation.guest_id == guest_id)
            existing = (await w.session.scalars(q)).first()
            if bool(change["on"]) == (existing is not None):
                continue  # already in the desired state
            if change["on"]:
                w.session.add(
                    Allocation(
                        id=uuid7(),
                        item_id=item_id,
                        member_id=member_id,
                        guest_id=guest_id,
                        created_by=user_id,
                        created_at=now,
                        updated_by_member_id=me.member_id,
                        updated_at=now,
                    )
                )
            else:
                if bill.state == "open":
                    assert existing is not None
                    others = await w.session.scalar(
                        select(func.count())
                        .select_from(Allocation)
                        .where(Allocation.item_id == item_id, Allocation.id != existing.id)
                    )
                    if not others:
                        raise Problem(
                            409,
                            ErrorCode.LAST_PERSON,
                            "Someone has to keep this one. Ask the payer to remove the item.",
                            extra={"item_id": str(item_id)},
                        )
                await w.session.delete(existing)
            applied.append(
                {"item_id": str(item_id), "participant": str(change["participant"]), "on": bool(change["on"])}
            )
            await w.session.flush()
        if not applied:
            return {"applied": [], "shares": None}
        # Ticks run concurrently (shared bill lock): serialize from here so the shares and the
        # snapshot are computed from every allocation committed before ours (SYNC-24).
        # Owners of the lines we changed follow now, while we hold those lines' locks (every other
        # change to their allocations needs them, or the exclusive bill lock), and before the house
        # lock: nothing touches owner rows after it, so a sharing switch can't deadlock with us.
        if bill.state == "open":
            await kitchen.sync_owners(w.session, rows, now, item_ids=sorted(locked))
        await w.lock_house()
        rows = await bill_math.reload_allocations(w.session, rows)
        deltas = await _after_money_change(w, rows, "allocation_change", now, owners=False)
        try:
            shares = bill_math.preview(rows).shares if bill.state == "draft" else bill_math.shares(rows).shares
        except MoneyError:
            shares = bill_math.preview(rows).shares
        w.emit(
            "allocation_changed",
            aggregate=("bill", bill.id, bill.version),
            data={"bill_id": str(bill.id), "changes": applied, "shares": shares, "share_deltas": deltas},
            activity=Activity(
                "allocation_changed", bill_id=bill.id, actor_user_id=user_id, payload={"changes": applied}
            )
            if bill.state == "open"
            else None,
            notify=["share"] if deltas else [],
        )
        return {"applied": applied, "shares": shares}


async def _bill_shared_lock(w: HouseWrite, bill_id: uuid.UUID) -> Bill:
    """Ticks take a SHARE lock on the bill: they run concurrently with each other but not with a
    settle/void/send (which take FOR UPDATE), so a tick after settle sees BILL_SETTLED (SYNC-06)."""
    bill = (await w.session.scalars(select(Bill).where(Bill.id == bill_id).with_for_update(read=True))).first()
    if bill is None or bill.deleted_at is not None:
        raise Problem(404, ErrorCode.NOT_FOUND, "Tab not found")
    return bill


async def fill_rest(
    writes: WriteContext, bill_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    """FR-21: everyone active (and not excluded) onto every item that has nobody. Only those change."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        rows = await bill_math.load(w.session, bill)
        everyone = [m for m in rows.members if m.status in ("active", "placeholder") and m.id not in rows.excluded]
        filled = [i for i in rows.unassigned]
        for item in filled:
            for m in everyone:
                w.session.add(
                    Allocation(
                        id=uuid7(),
                        item_id=item.id,
                        member_id=m.id,
                        created_by=user_id,
                        created_at=now,
                        updated_by_member_id=me.member_id,
                        updated_at=now,
                    )
                )
        await w.session.flush()
        rows = await bill_math.load(w.session, bill)
        deltas = await _after_money_change(w, rows, "fill_rest", now)
        if filled:
            w.emit(
                "allocation_changed",
                aggregate=("bill", bill.id, bill.version),
                data={"bill_id": str(bill.id), "filled_items": [str(i.id) for i in filled], "share_deltas": deltas},
                notify=["share"] if deltas else [],
            )
        out = {"filled": [str(i.id) for i in filled], "shares": bill_math.preview(rows).shares}
        w.respond(200, out)
    return out


async def set_exclusion(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    member_id: uuid.UUID,
    excluded: bool,
    now: datetime,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-26: hide a member's column for this bill (draft); their ticks on it are cleared."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        if bill.state != "draft":
            raise Problem(409, ErrorCode.CONFLICT, "Change who's on the tab before sending")
        member = await w.session.get(Member, member_id)
        if member is None or member.house_id != bill.house_id:  # SEC-02: only people in this house
            raise Problem(404, ErrorCode.NOT_FOUND, "That person isn't in this house")
        current = await w.session.get(BillExclusion, (bill_id, member_id))
        if excluded == (current is not None):
            return {"member_id": str(member_id), "excluded": excluded}
        if excluded:
            w.session.add(BillExclusion(bill_id=bill_id, member_id=member_id, created_at=now))
            item_ids = select(ItemRow.id).where(ItemRow.bill_id == bill_id)
            await w.session.execute(
                delete(Allocation).where(Allocation.member_id == member_id, Allocation.item_id.in_(item_ids))
            )
        else:
            await w.session.delete(current)
        w.emit(
            "bill_exclusion_changed",
            aggregate=("bill", bill.id, bill.version),
            data={"member_id": str(member_id), "excluded": excluded},
        )
        return {"member_id": str(member_id), "excluded": excluded}


async def add_guest(
    writes: WriteContext,
    pepper: str,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-25: a guest on this bill only, with a personal link; not a house member."""
    name = " ".join(name.split())
    if not 1 <= len(name) <= 24:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Names are 1 to 24 characters")
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_EDIT, actor=me, bill=bill_ctx(bill))
        count = await w.session.scalar(select(func.count()).select_from(Guest).where(Guest.bill_id == bill_id)) or 0
        guest_id = uuid7()
        token = derive_token(pepper, "guest", guest_id, now)
        guest = Guest(
            id=guest_id,
            bill_id=bill_id,
            display_name=name,
            link_token_hash=hash_token(token, pepper),
            sort_order=count,
            created_at=now,
        )
        w.session.add(guest)
        await w.session.flush()
        w.emit(
            "guest_added",
            aggregate=("bill", bill.id, bill.version),
            data={"guest_id": str(guest_id), "display_name": name},
            activity=Activity("guest_added", bill_id=bill.id, actor_user_id=user_id, payload={"name": name}),
        )
        out = {"id": str(guest_id), "key": gkey(guest_id), "display_name": name, "link_token": token}
        w.respond(201, out)
    return out


# ---- send -------------------------------------------------------------------------------------


async def send(
    writes: WriteContext,
    pepper: str,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-31: draft -> open. Every item needs someone (FR-24), totals must reconcile (FR-13).
    Creates the link, snapshots shares, stocks the kitchen (FR-61), notifies members once."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_SEND, actor=me, bill=bill_ctx(bill))
        rows = await bill_math.load(w.session, bill)
        if not rows.items:
            raise Problem(409, ErrorCode.CONFLICT, "Add at least one item first")
        if rows.unassigned:
            n = len(rows.unassigned)
            raise Problem(
                409,
                ErrorCode.CONFLICT,
                f"{n} {'item' if n == 1 else 'items'} looking for a home",
                extra={"unassigned": [str(i.id) for i in rows.unassigned]},
            )
        if not bill_math.reconciliation(rows)["ok"]:
            raise Problem(409, ErrorCode.CONFLICT, "The items don't add up to the receipt total yet")
        await _refresh_totals(w.session, rows, now)
        result = bill_math.shares(rows)
        token, token_hash = bill_token(pepper, bill.id, bill.created_at)
        bill.state = "open"
        bill.sent_at = now
        bill.link_token_hash = token_hash
        bill.version += 1
        for key, cents in result.shares.items():
            kind, _, raw = key.partition(":")
            w.session.add(
                ShareSnapshot(
                    id=uuid7(),
                    bill_id=bill.id,
                    member_id=uuid.UUID(raw) if kind == "m" else None,
                    guest_id=uuid.UUID(raw) if kind == "g" else None,
                    share_cents=cents,
                    bill_version=bill.version,
                    computed_at=now,
                    reason="sent",
                )
            )
        stocked = await kitchen.stock_bill(w.session, rows, now)
        w.emit(
            "bill_sent",
            aggregate=("bill", bill.id, bill.version),
            data={"bill_id": str(bill.id), "shares": result.shares, "stocked": stocked},
            activity=Activity(
                "bill_sent",
                bill_id=bill.id,
                actor_user_id=user_id,
                payload={"total_cents": bill.total_cents, "items": len(rows.items)},
            ),
            notify=["tab"],
        )
        out = {
            "id": str(bill.id),
            "state": "open",
            "version": bill.version,
            "link_token": token,
            "shares": result.shares,
            "stocked": stocked,
        }
        w.respond(200, out)
    return out
