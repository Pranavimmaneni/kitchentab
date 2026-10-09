"""House balances (PRD §6 payment rules; FR-50, FR-51; ADR-0001 pairwise netting).

Balance between A and B = Σ open-bill amounts owed between them + Σ ledger entries (carryover,
chip-in, void credits; settle-up is v1.1). Each pair is netted across every bill; there is no
simplification across three or more people. Voided bills contribute nothing; drafts don't count.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import MoneyError
from app.models import Bill, LedgerEntry, Member
from app.services import bill_math
from app.services.bill_life import owed_map, payments_for


@dataclass
class Pair:
    """Positive `net_cents` = the other member owes me."""

    member_id: uuid.UUID
    net_cents: int = 0
    open_bills: list[dict[str, Any]] = field(default_factory=list)
    carried_cents: int = 0


async def open_bill_debts(db: AsyncSession, house_id: uuid.UUID) -> list[tuple[Bill, uuid.UUID, uuid.UUID, int]]:
    """(bill, debtor member, creditor member, cents) for every open bill; guests excluded."""
    out: list[tuple[Bill, uuid.UUID, uuid.UUID, int]] = []
    bills = (
        await db.scalars(select(Bill).where(Bill.house_id == house_id, Bill.state == "open", Bill.deleted_at.is_(None)))
    ).all()
    for bill in bills:
        rows = await bill_math.load(db, bill)
        if rows.unassigned:
            continue
        try:
            owed = owed_map(rows, await payments_for(db, bill.id))
        except MoneyError:
            continue
        for key, cents in owed.items():
            if not key.startswith("m:") or cents == 0:
                continue
            member = uuid.UUID(key[2:])
            if cents > 0:
                out.append((bill, member, bill.payer_member_id, cents))
            else:
                out.append((bill, bill.payer_member_id, member, -cents))
    return out


async def pairs_for(db: AsyncSession, house_id: uuid.UUID, me: uuid.UUID) -> dict[uuid.UUID, Pair]:
    pairs: dict[uuid.UUID, Pair] = defaultdict(lambda: Pair(uuid.UUID(int=0)))

    def pair(other: uuid.UUID) -> Pair:
        p = pairs[other]
        p.member_id = other
        return p

    for bill, debtor, creditor, cents in await open_bill_debts(db, house_id):
        if me not in (debtor, creditor) or debtor == creditor:
            continue
        other = creditor if debtor == me else debtor
        signed = cents if creditor == me else -cents
        p = pair(other)
        p.net_cents += signed
        p.open_bills.append(
            {
                "bill_id": str(bill.id),
                "store_name": bill.store_name,
                "purchase_date": bill.purchase_date.isoformat(),
                "cents": signed,
            }
        )
    entries = (
        await db.scalars(
            select(LedgerEntry).where(
                LedgerEntry.house_id == house_id,
                (LedgerEntry.from_member_id == me) | (LedgerEntry.to_member_id == me),
            )
        )
    ).all()
    for e in entries:
        other = e.from_member_id if e.to_member_id == me else e.to_member_id
        signed = e.amount_cents if e.to_member_id == me else -e.amount_cents
        p = pair(other)
        p.net_cents += signed
        p.carried_cents += signed
    return dict(pairs)


async def balance_view(db: AsyncSession, house_id: uuid.UUID, me: uuid.UUID) -> dict[str, Any]:
    pairs = await pairs_for(db, house_id, me)
    members = {m.id: m for m in (await db.scalars(select(Member).where(Member.house_id == house_id))).all()}
    rows: list[dict[str, Any]] = []
    nets: list[int] = []
    for p in sorted(pairs.values(), key=lambda x: (-abs(x.net_cents), members[x.member_id].sort_order)):
        if p.net_cents == 0 and not p.open_bills and not p.carried_cents:
            continue
        m = members[p.member_id]
        nets.append(p.net_cents)
        rows.append(
            {
                "member_id": str(p.member_id),
                "display_name": m.display_name,
                "initial": m.display_name[:1].upper(),
                "color": m.color,
                "net_cents": p.net_cents,
                "carried_cents": p.carried_cents,
                "bills": p.open_bills,
            }
        )
    owed_to_me = sum(n for n in nets if n > 0)
    i_owe = -sum(n for n in nets if n < 0)
    return {
        "net_cents": owed_to_me - i_owe,
        "owed_to_me_cents": owed_to_me,
        "i_owe_cents": i_owe,
        "pairs": rows,
    }
