"""Kitchen gestures: moving items between Freezer, Fridge and Pantry, and each member's own order
(docs/features/kitchen-gestures/SPEC.md §5, ADR-0033).

* Moves are a state transition: one transaction locks the item rows in id order, applies every
  move it can and reports each one (`moved`, `skipped_already_there`, `skipped_gone`,
  `skipped_changed`). One `items.moved` event for the lot; no push (moves aren't news).
* The order is set-state: the client sends the whole list for one section; ids that are gone are
  skipped on read, never an error. Its event carries only the member and the section, so other
  members' clients ignore it and the member's other devices refetch.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ErrorCode, Problem
from app.models import InventoryItem, MemberItemOrder
from app.policy import Action, require
from app.services.house_write import Activity, Idempotency, WriteContext, house_write
from app.services.inventory import _me

Place = Literal["fridge", "freezer", "pantry"]
MOVE_MAX = 20  # STACK_MAX in the gesture engine
ORDER_MAX = 1000

MoveResult = Literal["moved", "skipped_already_there", "skipped_gone", "skipped_changed"]


@dataclass(frozen=True)
class Move:
    item_id: uuid.UUID
    to: Place
    # Undo only: move it back only if it's still where the first move put it (SYNC-G4). A first move
    # leaves this out, so two people moving the same item at once both succeed and the later one
    # wins (SYNC-G1).
    expect_from: Place | None = None


async def move_items(
    writes: WriteContext,
    house_id: uuid.UUID,
    user_id: uuid.UUID,
    moves: Sequence[Move],
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    if not 1 <= len(moves) <= MOVE_MAX:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, f"Move between 1 and {MOVE_MAX} items at a time")
    if len({m.item_id for m in moves}) != len(moves):
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Each item can only be moved once per request")
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _me(w, user_id)
        require(Action.ITEM_MOVE, actor=me)
        # Lock in id order so two overlapping moves can't deadlock (SYNC-G1).
        rows = {
            i.id: i
            for i in (
                await w.session.scalars(
                    select(InventoryItem)
                    .where(InventoryItem.id.in_([m.item_id for m in moves]), InventoryItem.house_id == house_id)
                    .order_by(InventoryItem.id)
                    .with_for_update(key_share=True)
                )
            ).all()
        }
        results: list[dict[str, Any]] = []
        moved: list[dict[str, Any]] = []
        for m in moves:
            item = rows.get(m.item_id)
            result: MoveResult
            if item is None or item.status != "in_stock":
                result = "skipped_gone"
            elif item.place == m.to:
                result = "skipped_already_there"
            elif m.expect_from is not None and item.place != m.expect_from:
                result = "skipped_changed"
            else:
                result = "moved"
                moved.append({"id": str(item.id), "from": item.place, "place": m.to})
                item.place = m.to
                item.moved_at = now
                item.moved_by_member_id = me.member_id
                item.updated_at = now
                item.version += 1
                moved[-1]["version"] = item.version
            results.append(
                {
                    "item_id": str(m.item_id),
                    "result": result,
                    "place": item.place if item is not None else None,
                    "version": item.version if item is not None else None,
                }
            )
        if moved:
            names = [rows[uuid.UUID(x["id"])].name for x in moved]
            w.emit(
                "items.moved",
                aggregate=("inventory", house_id, None),
                data={"moves": moved, "by": str(me.member_id)},
                activity=Activity(
                    "items_moved",
                    payload={"names": names, "to": sorted({x["place"] for x in moved})},
                    actor_user_id=user_id,
                ),
            )
        out = {"results": results, "moved": len(moved)}
        w.respond(200, out)
    return out


async def set_item_order(
    writes: WriteContext,
    house_id: uuid.UUID,
    user_id: uuid.UUID,
    location: Place,
    item_ids: Sequence[uuid.UUID],
    now: datetime,
    cmid: str | None,
) -> dict[str, Any]:
    """Set-state PUT of my order for one section (SYNC-G5: the last write wins per location)."""
    if len(item_ids) > ORDER_MAX:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, f"At most {ORDER_MAX} items")
    if len(set(item_ids)) != len(item_ids):
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Each item can appear once")
    ids = list(item_ids)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await _me(w, user_id)
        require(Action.ITEM_ORDER_SET, actor=me)
        key = (house_id, me.member_id, location)
        current = await w.session.get(MemberItemOrder, key, with_for_update=True)
        if current is not None and list(current.item_ids) == ids:
            return {"location": location, "item_ids": [str(i) for i in ids]}
        await w.session.execute(
            insert(MemberItemOrder)
            .values(house_id=house_id, member_id=me.member_id, location=location, item_ids=ids, updated_at=now)
            .on_conflict_do_update(
                index_elements=["house_id", "member_id", "location"],
                set_={"item_ids": ids, "updated_at": now},
            )
        )
        w.emit(
            "member.item_order.set",
            # Not a "member" aggregate: that would make every client refetch members and bills.
            aggregate=("item_order", me.member_id, None),
            data={"member_id": str(me.member_id), "location": location},
        )
    return {"location": location, "item_ids": [str(i) for i in ids]}


async def my_orders(db: AsyncSession, house_id: uuid.UUID, member_id: uuid.UUID) -> dict[str, list[uuid.UUID]]:
    rows = (
        await db.scalars(
            select(MemberItemOrder).where(MemberItemOrder.house_id == house_id, MemberItemOrder.member_id == member_id)
        )
    ).all()
    return {r.location: list(r.item_ids) for r in rows}


def apply_order(views: list[dict[str, Any]], saved: Sequence[uuid.UUID] | None) -> list[dict[str, Any]]:
    """Saved ids first in their order (gone ones skipped), then everything else newest first. With
    nothing saved, the screen keeps its usual order."""
    if not saved:
        return views
    rank = {str(i): n for n, i in enumerate(saved)}
    listed = sorted((v for v in views if v["id"] in rank), key=lambda v: rank[v["id"]])
    rest = sorted((v for v in views if v["id"] not in rank), key=lambda v: v["created_at"], reverse=True)
    return listed + rest
