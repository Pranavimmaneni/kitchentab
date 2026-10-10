"""Fridge and pantry stocking that rides on bill writes (PRD §19.1-§19.2; FR-61, FR-62).

Called inside the bill's write transaction so the kitchen never disagrees with the bill. Owners
are the members allocated to the line; guests never own kitchen items. Settled bills freeze owners
(sync is only called while the bill is open). Milestone 8 adds asks, grants and the screens.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.kitchen import default_shelf_life, generic_icon, place_for, short_name
from app.ids import uuid7
from app.models import InventoryGrant, InventoryItem, InventoryOwner, Member, ShareRequest, User
from app.models import Item as ItemRow
from app.services.bill_math import BillRows


async def _share_defaults(db: AsyncSession, member_ids: list[uuid.UUID]) -> dict[uuid.UUID, bool]:
    """FR-70: each owner's "open to share new items by default" (placeholders: off)."""
    if not member_ids:
        return {}
    rows = (
        await db.execute(
            select(Member.id, User.share_by_default)
            .join(User, User.id == Member.user_id, isouter=True)
            .where(Member.id.in_(member_ids))
        )
    ).all()
    return {mid: bool(default) for mid, default in rows}


def _owner_ids(rows: BillRows, item_id: uuid.UUID) -> list[uuid.UUID]:
    return [a.member_id for a in rows.allocations_by_item().get(item_id, []) if a.member_id is not None]


async def stock_item(db: AsyncSession, rows: BillRows, item: ItemRow, now: datetime) -> bool:
    """One inventory item per consumable line (not NOT FOOD). Returns True if stocked."""
    if item.storage == "none" or item.deleted_at is not None:
        return False
    existing = (await db.scalars(select(InventoryItem).where(InventoryItem.item_id == item.id))).first()
    if existing is not None:
        if existing.status == "removed":  # retagged back from NOT FOOD
            existing.status = "in_stock"
            existing.updated_at = now
        return False
    inv = InventoryItem(
        id=uuid7(),
        house_id=rows.bill.house_id,
        bill_id=rows.bill.id,
        item_id=item.id,
        place=place_for(item.storage),
        category=item.category,
        icon_key=item.icon_key or generic_icon(item.storage),
        name=item.name,
        short_name=short_name(item.name),
        quantity_text=None if item.quantity == 1 else f"{item.quantity:g}",
        price_cents=item.line_total_cents,
        stocked_on=rows.bill.purchase_date,
        shelf_life_days=item.shelf_life_days or default_shelf_life(item.category, item.icon_key),
        created_by=rows.bill.payer_member_id,
        created_at=now,
        updated_at=now,
    )
    db.add(inv)
    await db.flush()
    owners = _owner_ids(rows, item.id)
    defaults = await _share_defaults(db, owners)
    for member_id in owners:
        db.add(
            InventoryOwner(
                inventory_item_id=inv.id,
                member_id=member_id,
                open_to_share=defaults.get(member_id, False),
                updated_by_member_id=member_id,
                updated_at=now,
            )
        )
    return True


async def stock_bill(db: AsyncSession, rows: BillRows, now: datetime) -> int:
    """On send (FR-61)."""
    stocked = 0
    for item in rows.items:
        stocked += await stock_item(db, rows, item, now)
    return stocked


async def _close_access(db: AsyncSession, inventory_item_id: uuid.UUID, now: datetime) -> None:
    """Grants are dropped and pending asks expire when an item leaves the kitchen (EDGE-19-06)."""
    await db.execute(delete(InventoryGrant).where(InventoryGrant.inventory_item_id == inventory_item_id))
    await db.execute(
        update(ShareRequest)
        .where(ShareRequest.inventory_item_id == inventory_item_id, ShareRequest.status == "pending")
        .values(status="expired", answered_at=now)
    )


async def unstock_item(db: AsyncSession, item: ItemRow, now: datetime) -> None:
    """Line deleted or retagged NOT FOOD on an open bill: the item leaves unless already used up."""
    inv = (
        await db.scalars(select(InventoryItem).where(InventoryItem.item_id == item.id).with_for_update(key_share=True))
    ).first()
    if inv is None or inv.status != "in_stock":
        return
    inv.status = "removed"
    inv.updated_at = now
    inv.version += 1
    await _close_access(db, inv.id, now)


async def restock_item(db: AsyncSession, rows: BillRows, item: ItemRow, now: datetime) -> None:
    """After a payer edit on an open bill: follow the storage tag and the name."""
    if item.storage == "none":
        await unstock_item(db, item, now)
        return
    inv = (await db.scalars(select(InventoryItem).where(InventoryItem.item_id == item.id))).first()
    if inv is None or inv.status == "removed":
        await stock_item(db, rows, item, now)
        return
    inv.place = place_for(item.storage)
    inv.name = item.name
    inv.short_name = short_name(item.name)
    inv.price_cents = item.line_total_cents
    inv.updated_at = now


async def sync_owners(
    db: AsyncSession, rows: BillRows, now: datetime, *, item_ids: list[uuid.UUID] | None = None
) -> None:
    """FR-62: owners follow allocations while the bill is open, in the same transaction.

    One statement (two data-modifying CTEs over one snapshot): add owners for allocated members,
    drop owners without an allocation. Reading owners and allocations in separate SELECTs let a
    concurrent commit land in between (READ COMMITTED; found by SYNC-24). `item_ids` limits it to
    the lines a tick touched, whose row locks the tick already holds (ADR-0023).
    """
    if rows.bill.state != "open":
        return
    await db.flush()
    scope = "AND i.item_id = ANY(:items) " if item_ids is not None else ""
    sql = (
        "WITH added AS ("  # noqa: S608 - `scope` is a constant fragment
        "INSERT INTO inventory_owners "
        "(inventory_item_id, member_id, open_to_share, updated_by_member_id, updated_at) "
        "SELECT DISTINCT i.id, a.member_id, COALESCE(u.share_by_default, false), a.member_id, "
        "CAST(:now AS timestamptz) "
        "FROM inventory_items i "
        "JOIN items it ON it.id = i.item_id AND it.deleted_at IS NULL "
        "JOIN allocations a ON a.item_id = i.item_id AND a.member_id IS NOT NULL "
        "JOIN members m ON m.id = a.member_id "
        "LEFT JOIN users u ON u.id = m.user_id "
        "WHERE i.bill_id = :bill AND i.status = 'in_stock' " + scope + ""
        "ON CONFLICT (inventory_item_id, member_id) DO NOTHING RETURNING 1), "
        "dropped AS ("
        "DELETE FROM inventory_owners o USING inventory_items i "
        "WHERE o.inventory_item_id = i.id AND i.bill_id = :bill AND i.status = 'in_stock' "
        "AND i.item_id IS NOT NULL " + scope + ""
        "AND NOT EXISTS (SELECT 1 FROM allocations a WHERE a.item_id = i.item_id AND a.member_id = o.member_id) "
        "RETURNING 1) "
        "SELECT (SELECT count(*) FROM added), (SELECT count(*) FROM dropped)"
    )
    params: dict[str, object] = {"bill": rows.bill.id, "now": now}
    if item_ids is not None:
        params["items"] = item_ids
    await db.execute(text(sql), params)
    for obj in [o for o in db.identity_map.values() if isinstance(o, InventoryOwner)]:
        db.expunge(obj)  # owners changed under the ORM; later reads fetch them fresh
