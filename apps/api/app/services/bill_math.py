"""Load a bill's rows and run `compute_shares` on them (PRD §6). Server-side only (rule 5).

Participants are keyed `m:<member id>` / `g:<guest id>` so members and guests share one
namespace. Rounding ties break by `sort_order` then key; guests' sort order continues after the
house's members (ADR-0003).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Allocation as MAlloc
from app.domain.money import BillInput, Fee, Item, Participant, ShareResult, compute_shares, weight_to_milli
from app.models import Allocation, Bill, BillExclusion, BillFee, Guest, Member
from app.models import Item as ItemRow


def mkey(member_id: uuid.UUID) -> str:
    return f"m:{member_id}"


def gkey(guest_id: uuid.UUID) -> str:
    return f"g:{guest_id}"


@dataclass
class BillRows:
    bill: Bill
    items: list[ItemRow]
    allocations: list[Allocation]
    fees: list[BillFee]
    members: list[Member]
    guests: list[Guest]
    excluded: set[uuid.UUID] = field(default_factory=set)

    def participants(self) -> tuple[Participant, ...]:
        out = [Participant(mkey(m.id), m.sort_order) for m in self.members]
        base = max((m.sort_order for m in self.members), default=-1) + 1
        out += [Participant(gkey(g.id), base + g.sort_order) for g in self.guests]
        return tuple(out)

    def allocations_by_item(self) -> dict[uuid.UUID, list[Allocation]]:
        out: dict[uuid.UUID, list[Allocation]] = {i.id: [] for i in self.items}
        for a in self.allocations:
            out.setdefault(a.item_id, []).append(a)
        return out

    @property
    def unassigned(self) -> list[ItemRow]:
        by_item = self.allocations_by_item()
        return [i for i in self.items if not by_item.get(i.id)]


async def load(db: AsyncSession, bill: Bill) -> BillRows:
    items = list(
        (
            await db.scalars(
                select(ItemRow)
                .where(ItemRow.bill_id == bill.id, ItemRow.deleted_at.is_(None))
                .order_by(ItemRow.position)
            )
        ).all()
    )
    item_ids = [i.id for i in items]
    allocations = (
        list((await db.scalars(select(Allocation).where(Allocation.item_id.in_(item_ids)))).all()) if item_ids else []
    )
    fees = list((await db.scalars(select(BillFee).where(BillFee.bill_id == bill.id).order_by(BillFee.position))).all())
    members = list(
        (await db.scalars(select(Member).where(Member.house_id == bill.house_id).order_by(Member.sort_order))).all()
    )
    guests = list((await db.scalars(select(Guest).where(Guest.bill_id == bill.id).order_by(Guest.sort_order))).all())
    excluded = set((await db.scalars(select(BillExclusion.member_id).where(BillExclusion.bill_id == bill.id))).all())
    return BillRows(bill, items, allocations, fees, members, guests, excluded)


def level_fees(rows: BillRows) -> tuple[Fee, ...]:
    return tuple(Fee(f.kind, f.label, f.amount_cents) for f in rows.fees)  # type: ignore[arg-type]


def computed_total(rows: BillRows, *, include_adjustment: bool = True) -> int:
    total = sum(i.line_total_cents for i in rows.items) + rows.bill.tax_cents
    for f in rows.fees:
        if f.kind == "adjustment" and not include_adjustment:
            continue
        total += -f.amount_cents if f.kind == "order_discount" else f.amount_cents
    return total


def _input(rows: BillRows, items: list[ItemRow], total: int) -> BillInput:
    by_item = rows.allocations_by_item()

    def key(a: Allocation) -> str:
        return mkey(a.member_id) if a.member_id else gkey(a.guest_id)  # type: ignore[arg-type]

    return BillInput(
        participants=rows.participants(),
        items=tuple(
            Item(
                str(i.id),
                i.line_total_cents,
                tuple(MAlloc(key(a), weight_to_milli(Decimal(a.weight))) for a in by_item[i.id]),
            )
            for i in items
        ),
        tax_cents=rows.bill.tax_cents,
        fees=level_fees(rows),
        total_cents=total,
    )


def shares(rows: BillRows) -> ShareResult:
    """Authoritative shares; every item must have someone (send, open bills)."""
    return compute_shares(_input(rows, rows.items, rows.bill.total_cents))


def preview(rows: BillRows) -> ShareResult:
    """Live totals while splitting: only assigned items count; bill-level amounts follow them."""
    assigned = [i for i in rows.items if rows.allocations_by_item()[i.id]]
    if not assigned:
        return ShareResult({p.id: 0 for p in rows.participants()}, {p.id: 0 for p in rows.participants()})
    subtotal = sum(i.line_total_cents for i in assigned)
    levels = computed_total(rows) - sum(i.line_total_cents for i in rows.items)
    return compute_shares(_input(rows, assigned, subtotal + levels))


def reconciliation(rows: BillRows) -> dict[str, Any]:
    """FR-13: items + tax + fees - discounts vs the receipt total."""
    computed = computed_total(rows, include_adjustment=False)
    printed = rows.bill.receipt_total_cents
    adjustment = next((f.amount_cents for f in rows.fees if f.kind == "adjustment"), 0)
    difference = 0 if printed is None else printed - computed
    return {
        "receipt_total_cents": printed,
        "computed_cents": computed,
        "difference_cents": difference,
        "overridden": rows.bill.printed_total_cents is not None,
        "override_reason": rows.bill.reconcile_override_reason,
        "adjustment_cents": adjustment,
        "ok": difference == 0 or rows.bill.printed_total_cents is not None,
    }
