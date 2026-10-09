"""The apt4b scenario reproduces the mockups' numbers under the reference money rules."""

from __future__ import annotations

import importlib.util
import uuid
from collections import defaultdict
from types import ModuleType

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import InventoryItem, Member, ShareRequest
from app.seed.scenario import BillSpec, Scenario, load_scenario, seed_uuid
from app.seed.writer import write_scenario
from app.settings import REPO_ROOT


def _reference() -> ModuleType:
    path = REPO_ROOT / "docs" / "reference" / "compute_shares_reference.py"
    spec = importlib.util.spec_from_file_location("compute_shares_reference", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shares(ref: ModuleType, sc: Scenario, bill: BillSpec) -> dict[str, int]:
    participants = [{"id": key, "sort_order": i} for i, key in enumerate(sc.member_keys)]
    items = [
        {"id": it.key, "line_total_cents": it.cents, "allocations": [{"participant": w, "weight": 1} for w in it.who]}
        for it in bill.items
    ]
    result = ref.compute_shares({"participants": participants, "items": items, "tax_cents": bill.tax_cents,
                                 "fees": [], "total_cents": bill.total_cents})
    return dict(result["shares"])


def test_bills_reproduce_expected_shares_with_the_reference() -> None:
    ref, sc = _reference(), load_scenario("apt4b")
    for bill in sc.bills:
        assert _shares(ref, sc, bill) == bill.expected_shares, bill.key


def test_tabs_card_nets_to_mockup_balance() -> None:
    """Main.dc.html: ARJUN → YOU $33.91, MEERA → YOU $8.14, YOU → KABIR $1.68, net +$40.37."""
    ref, sc = _reference(), load_scenario("apt4b")
    me = sc.raw["expected_balances_for"]
    pairs: dict[str, int] = defaultdict(int)
    for bill in sc.bills:
        if bill.state != "open":
            continue
        shares = _shares(ref, sc, bill)
        paid: dict[str, int] = defaultdict(int)
        for p in bill.payments:
            if p.status == "confirmed":
                paid[p.member] += p.amount_cents
        for member, share in shares.items():
            if member == bill.payer:
                continue
            owed = share - paid[member]
            if bill.payer == me:
                pairs[member] += owed
            elif member == me:
                pairs[bill.payer] -= owed
    assert dict(pairs) == sc.raw["expected_pairs"]
    assert sum(pairs.values()) == sc.raw["expected_net"] == 4037


async def test_seeded_kitchen_matches_mockup_counts(sessions: async_sessionmaker[AsyncSession]) -> None:
    sc = load_scenario("apt4b")
    async with sessions() as s, s.begin():
        ids = await write_scenario(s, sc, "pepper")
    async with sessions() as s:
        counts = dict(
            (await s.execute(
                select(InventoryItem.place, func.count()).where(InventoryItem.status == "in_stock").group_by(InventoryItem.place)
            )).all()
        )
        pending = await s.scalar(select(func.count()).select_from(ShareRequest).where(ShareRequest.status == "pending"))
        colors = (await s.scalars(select(Member.color).order_by(Member.sort_order))).all()
    # Kitchen.dc.html: FRIDGE 10 ITEMS (freezer + refrigerated), PANTRY 5 ITEMS.
    assert counts["fridge"] + counts["freezer"] == 10 and counts["pantry"] == 5
    assert pending == 3
    assert list(colors) == ["#C6F432", "#6A4CFF", "#FF8A65", "#7FD3F7"]
    assert isinstance(ids["bill:shoprite"], uuid.UUID)


def test_seed_ids_are_deterministic_uuid7() -> None:
    sc = load_scenario("apt4b")
    a = seed_uuid("apt4b", "member", "pranav", sc.now)
    assert a == seed_uuid("apt4b", "member", "pranav", sc.now)
    assert a != seed_uuid("apt4b", "member", "arjun", sc.now)
    assert a.version == 7
