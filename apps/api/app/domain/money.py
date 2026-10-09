"""Split math (PRD §6): the single source of truth for shares. Integer cents only.

Must produce exactly what `docs/reference/compute_shares_reference.py` produces for every bill
(fixtures in `packages/fixtures/money_cases.json`, property corpus checked by digest). The
TypeScript mirror in `apps/web/lib/money/` follows the same steps line for line.

Weights are stored as numeric(6,3); here they are integer thousandths (`weight_milli`), so every
step is exact integer arithmetic: no floats, no Fractions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

FeeKind = Literal["fee", "tip", "order_discount", "adjustment"]
WEIGHT_SCALE = 1000


class MoneyError(ValueError):
    pass


class ReconciliationError(MoneyError):
    """Items + bill-level amounts don't add up to the bill total (FR-13 needs fixing or an override)."""


class InvariantError(MoneyError):
    """Σ shares ≠ total. Never silently adjusted (§6 step 5)."""


@dataclass(frozen=True, slots=True)
class Participant:
    id: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class Allocation:
    participant: str
    weight_milli: int = WEIGHT_SCALE


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    line_total_cents: int
    allocations: tuple[Allocation, ...]


@dataclass(frozen=True, slots=True)
class Fee:
    kind: FeeKind
    label: str
    amount_cents: int  # positive except kind="adjustment", which is signed (ADR-0002)


@dataclass(frozen=True, slots=True)
class BillInput:
    participants: tuple[Participant, ...]
    items: tuple[Item, ...]
    tax_cents: int
    fees: tuple[Fee, ...]
    total_cents: int


@dataclass(frozen=True, slots=True)
class ShareResult:
    item_subtotals: dict[str, int]
    shares: dict[str, int]
    by_item: dict[str, dict[str, int]] = field(default_factory=dict)


def weight_to_milli(weight: int | str | Decimal) -> int:
    value = Decimal(str(weight)) * WEIGHT_SCALE
    if value != value.to_integral_value() or value <= 0:
        raise MoneyError(f"weight must be positive with at most 3 decimals: {weight}")
    return int(value)


def largest_remainder(
    amount: int, weights: Mapping[str, int], order: Mapping[str, tuple[int, str]]
) -> dict[str, int]:
    """Split integer `amount` across `weights` (positive integers) exactly.

    Each key gets floor(|amount| * w / W); the leftover cents go one at a time to the largest
    remainders, ties broken by (sort_order, id). Negative amounts are split on their absolute
    value, then negated.
    """
    if not weights:
        return {}
    if amount == 0:
        return dict.fromkeys(weights, 0)
    total_weight = sum(weights.values())
    if total_weight <= 0 or any(w <= 0 for w in weights.values()):
        raise MoneyError("weights must be positive")
    sign = -1 if amount < 0 else 1
    magnitude = abs(amount)
    floors: dict[str, int] = {}
    remainders: dict[str, int] = {}
    for key, w in weights.items():
        floors[key], remainders[key] = divmod(magnitude * w, total_weight)
    left = magnitude - sum(floors.values())
    for key in sorted(weights, key=lambda k: (-remainders[k], order[k]))[:left]:
        floors[key] += 1
    return {k: sign * v for k, v in floors.items()}


def bill_level_amounts(bill: BillInput) -> list[tuple[str, int]]:
    """Tax first, then each fee in order; order discounts negated, adjustments as stored."""
    out = [("tax", bill.tax_cents)]
    for fee in bill.fees:
        out.append((fee.label, -fee.amount_cents if fee.kind == "order_discount" else fee.amount_cents))
    return out


def compute_shares(bill: BillInput) -> ShareResult:
    order = {p.id: (p.sort_order, p.id) for p in bill.participants}
    item_sub = dict.fromkeys(order, 0)
    by_item: dict[str, dict[str, int]] = {}
    allocated_any: list[str] = []
    seen: set[str] = set()

    for item in bill.items:
        if not item.allocations:
            raise MoneyError(f"item {item.id} has no allocations")
        weights: dict[str, int] = {}
        for a in item.allocations:
            if a.participant not in order:
                raise MoneyError(f"item {item.id} allocated to unknown participant {a.participant}")
            if a.participant in weights:
                raise MoneyError(f"item {item.id} allocates {a.participant} twice")
            weights[a.participant] = a.weight_milli
        split = largest_remainder(item.line_total_cents, weights, order)
        by_item[item.id] = split
        for pid, cents in split.items():
            item_sub[pid] += cents
            if pid not in seen:
                seen.add(pid)
                allocated_any.append(pid)

    clipped = {pid: max(item_sub[pid], 0) for pid in allocated_any}
    if sum(clipped.values()) == 0:
        clipped = dict.fromkeys(allocated_any, 1)
    # largest_remainder needs positive weights: drop zero-weight participants for this step.
    positive = {pid: w for pid, w in clipped.items() if w > 0}

    shares = dict(item_sub)
    levels = bill_level_amounts(bill)
    for _, amount in levels:
        if amount and not positive:
            raise MoneyError("bill-level amounts but no allocated participants")
        for pid, cents in largest_remainder(amount, positive, order).items():
            shares[pid] += cents

    expected_total = sum(i.line_total_cents for i in bill.items) + sum(a for _, a in levels)
    if expected_total != bill.total_cents:
        raise ReconciliationError(
            f"bill does not reconcile: computed {expected_total} vs total {bill.total_cents}"
        )
    if sum(shares.values()) != bill.total_cents:
        raise InvariantError("share invariant violated")
    return ShareResult(item_sub, shares, by_item)


# ---- adapters ------------------------------------------------------------------------------


def bill_from_dict(raw: Mapping[str, Any]) -> BillInput:
    """The fixture / reference JSON shape (`packages/fixtures/money_cases.json`)."""
    return BillInput(
        participants=tuple(Participant(str(p["id"]), int(p["sort_order"])) for p in raw["participants"]),
        items=tuple(
            Item(
                str(i["id"]),
                int(i["line_total_cents"]),
                tuple(
                    Allocation(str(a["participant"]), weight_to_milli(a.get("weight", 1)))
                    for a in i["allocations"]
                ),
            )
            for i in raw["items"]
        ),
        tax_cents=int(raw.get("tax_cents", 0)),
        fees=tuple(Fee(f["kind"], str(f["label"]), int(f["amount_cents"])) for f in raw.get("fees", [])),
        total_cents=int(raw["total_cents"]),
    )
