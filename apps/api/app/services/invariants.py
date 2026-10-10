"""Invariant checker (TEST_PLAN DATA-05; BUILD_ALL §4.3). Reads only; returns violations as text.

  money      Σ shares = total on every sent bill, and the stored share snapshot equals a fresh
             compute_shares (open and settled bills)
  kitchen    owners of an in-stock item from an open bill = members allocated on its line
  balances   per house, everyone's net sums to zero (what one is owed, another owes)
  events     per house, sequence numbers are gapless up to houses.event_seq
  notify     every notification tied to an event points at a committed event

Used by `scripts/check_invariants.py`, the restore drill and the household simulation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import MoneyError
from app.models import Allocation, Bill, House, HouseEvent, InventoryItem, InventoryOwner, Member, Notification
from app.services import bill_math
from app.services.balances import balance_view
from app.services.bills import latest_snapshot


async def check_money(db: AsyncSession) -> list[str]:
    out: list[str] = []
    bills = (await db.scalars(select(Bill).where(Bill.state.in_(("open", "settled")), Bill.deleted_at.is_(None)))).all()
    for bill in bills:
        rows = await bill_math.load(db, bill)
        try:
            result = bill_math.shares(rows)
        except MoneyError as exc:
            out.append(f"bill {bill.id}: shares can't be computed ({exc})")
            continue
        if sum(result.shares.values()) != bill.total_cents:
            out.append(f"bill {bill.id}: Σ shares {sum(result.shares.values())} != total {bill.total_cents}")
        stored = {k: v for k, v in (await latest_snapshot(db, bill.id)).items() if v}
        fresh = {k: v for k, v in result.shares.items() if v}
        if stored != fresh:
            out.append(f"bill {bill.id}: stored shares {stored} != computed {fresh}")
    return out


async def check_kitchen(db: AsyncSession) -> list[str]:
    out: list[str] = []
    items = (
        await db.execute(
            select(InventoryItem, Bill)
            .join(Bill, Bill.id == InventoryItem.bill_id)
            .where(InventoryItem.status == "in_stock", Bill.state == "open", InventoryItem.item_id.is_not(None))
        )
    ).all()
    for item, _bill in items:
        owners = set(
            (
                await db.scalars(select(InventoryOwner.member_id).where(InventoryOwner.inventory_item_id == item.id))
            ).all()
        )
        allocated = set(
            (
                await db.scalars(
                    select(Allocation.member_id).where(
                        Allocation.item_id == item.item_id, Allocation.member_id.is_not(None)
                    )
                )
            ).all()
        )
        if owners != allocated:
            out.append(
                f"inventory {item.id}: owners {sorted(map(str, owners))} != allocated {sorted(map(str, allocated))}"
            )
    return out


async def check_balances(db: AsyncSession) -> list[str]:
    out: list[str] = []
    for house_id in (await db.scalars(select(House.id))).all():
        members = (await db.scalars(select(Member.id).where(Member.house_id == house_id))).all()
        total = 0
        for member_id in members:
            total += int((await balance_view(db, house_id, member_id))["net_cents"])
        if total != 0:
            out.append(f"house {house_id}: member balances sum to {total}, not 0")
    return out


async def check_events(db: AsyncSession) -> list[str]:
    out: list[str] = []
    for house in (await db.scalars(select(House))).all():
        seqs = list(
            (
                await db.scalars(select(HouseEvent.seq).where(HouseEvent.house_id == house.id).order_by(HouseEvent.seq))
            ).all()
        )
        if not seqs:
            continue
        if seqs != list(range(seqs[0], seqs[0] + len(seqs))):
            out.append(f"house {house.id}: event sequence has gaps or duplicates")
        if seqs[-1] != house.event_seq:
            out.append(f"house {house.id}: last event {seqs[-1]} != houses.event_seq {house.event_seq}")
    return out


async def check_notifications(db: AsyncSession, now: datetime) -> list[str]:
    """Events are kept 7 days (retention), so only recent notifications are checked."""
    orphans = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .outerjoin(
            HouseEvent,
            (HouseEvent.house_id == Notification.house_id) & (HouseEvent.seq == Notification.event_seq),
        )
        .where(
            Notification.event_seq.is_not(None),
            Notification.created_at > now - timedelta(days=6),
            HouseEvent.seq.is_(None),
        )
    )
    return [f"{orphans} notifications point at no committed event"] if orphans else []


async def check_all(db: AsyncSession, now: datetime) -> list[str]:
    return [
        *await check_money(db),
        *await check_kitchen(db),
        *await check_balances(db),
        *await check_events(db),
        *await check_notifications(db, now),
    ]


def short(member_id: uuid.UUID) -> str:  # pragma: no cover - log helper
    return str(member_id)[:8]
