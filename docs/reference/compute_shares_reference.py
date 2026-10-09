"""Reference implementation of KitchenTab's split math (PRD section 6).

This file is the executable spec. The production `apps/api/app/domain/money.py`
and `apps/web/lib/money/` must produce identical output for every case in
`packages/fixtures/money_cases.json`. Run this file to regenerate and verify
the fixtures:  python3 docs/reference/compute_shares_reference.py

Rules encoded here:
- All money is integer cents. No floats touch money.
- Item line totals are split by weight using largest-remainder rounding.
- Ties on remainder break by participant sort_order, then participant id.
- Bill-level amounts (tax, each fee/tip, each order discount) are allocated one
  at a time by each participant's item subtotal clipped at 0. If every clipped
  subtotal is 0, they are split equally among participants with any allocation.
- Negative amounts are rounded on their absolute value, then negated.
- Invariant: sum(shares) == bill total. Violations raise.
"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path


def largest_remainder(amount: int, weights: dict[str, Fraction], order: dict[str, tuple]) -> dict[str, int]:
    """Split integer `amount` across keys of `weights` proportionally, exactly."""
    if amount == 0 or not weights:
        return {k: 0 for k in weights}
    sign = -1 if amount < 0 else 1
    total = abs(amount)
    wsum = sum(weights.values())
    if wsum <= 0:
        raise ValueError("weights must sum to a positive number")
    exact = {k: Fraction(total) * w / wsum for k, w in weights.items()}
    floors = {k: v.numerator // v.denominator for k, v in exact.items()}
    left = total - sum(floors.values())
    ranked = sorted(weights, key=lambda k: (-(exact[k] - floors[k]), order[k]))
    for k in ranked[:left]:
        floors[k] += 1
    return {k: sign * v for k, v in floors.items()}


def compute_shares(bill: dict) -> dict:
    participants = bill["participants"]  # [{id, sort_order}]
    order = {p["id"]: (p["sort_order"], p["id"]) for p in participants}
    item_sub = {p["id"]: 0 for p in participants}
    allocated_any: set[str] = set()

    for item in bill["items"]:
        alloc = item["allocations"]  # [{participant, weight}]
        if not alloc:
            raise ValueError(f"item {item['id']} has no allocations")
        weights = {a["participant"]: Fraction(str(a.get("weight", 1))) for a in alloc}
        for pid, cents in largest_remainder(item["line_total_cents"], weights, order).items():
            item_sub[pid] += cents
        allocated_any.update(weights)

    shares = dict(item_sub)
    clipped = {pid: Fraction(max(v, 0)) for pid, v in item_sub.items() if pid in allocated_any}
    if sum(clipped.values()) == 0:
        clipped = {pid: Fraction(1) for pid in allocated_any}

    bill_level = [("tax", bill.get("tax_cents", 0))]
    for f in bill.get("fees", []):  # kind: fee | tip | order_discount
        amt = -f["amount_cents"] if f["kind"] == "order_discount" else f["amount_cents"]
        bill_level.append((f["label"], amt))
    for _, amt in bill_level:
        for pid, cents in largest_remainder(amt, clipped, order).items():
            shares[pid] += cents

    expected_total = sum(i["line_total_cents"] for i in bill["items"]) + sum(a for _, a in bill_level)
    if expected_total != bill["total_cents"]:
        raise ValueError(f"bill does not reconcile: computed {expected_total} vs total {bill['total_cents']}")
    if sum(shares.values()) != bill["total_cents"]:
        raise AssertionError("share invariant violated")
    return {"item_subtotals": item_sub, "shares": shares}


# ---------------------------------------------------------------- fixtures ---
P = [{"id": "pranav", "sort_order": 0}, {"id": "arjun", "sort_order": 1},
     {"id": "meera", "sort_order": 2}, {"id": "kabir", "sort_order": 3}]


def item(iid, cents, who, weights=None):
    return {"id": iid, "line_total_cents": cents,
            "allocations": [{"participant": w, "weight": (weights or {}).get(w, 1)} for w in who]}


ALL = ["pranav", "arjun", "meera", "kabir"]
SHOPRITE_ITEMS = [
    item("milk", 429, ["pranav", "arjun", "meera"]),
    item("eggs", 549, ALL),
    item("rice", 1499, ALL),
    item("yogurt", 679, ["meera"]),
    item("bananas", 132, ["pranav", "kabir"]),
    item("towels", 999, ALL),
    item("soap", 349, ALL),
    item("chicken", 1137, ["pranav", "arjun"]),
    item("oatmilk", 459, ["kabir"]),
    item("spinach", 399, ["pranav", "meera"]),
    item("bars", 1799, ["arjun"]),
    item("coffee", 999, ["pranav", "kabir"]),
]

CASES = [
    {"name": "shoprite_sent", "note": "Mockup bill as sent (paid by pranav)",
     "bill": {"participants": P, "items": SHOPRITE_ITEMS, "tax_cents": 89, "fees": [], "total_cents": 9518}},
    {"name": "shoprite_after_meera_joins_coffee", "note": "Meera adds herself to coffee beans after send",
     "bill": {"participants": P, "items": [i if i["id"] != "coffee" else item("coffee", 999, ["pranav", "meera", "kabir"]) for i in SHOPRITE_ITEMS],
              "tax_cents": 89, "fees": [], "total_cents": 9518}},
    {"name": "request_add_to_split", "note": "Fridge & pantry (PRD 19.4): owner approves Kabir's ask for chicken thighs with 'Add to split' on the bill after Meera joined the coffee. Expected deltas vs shoprite_after_meera_joins_coffee: kabir +383, pranav -192, arjun -191",
     "bill": {"participants": P, "items": [i if i["id"] != "chicken" else item("chicken", 1137, ["pranav", "arjun", "kabir"]) for i in
                                           [i if i["id"] != "coffee" else item("coffee", 999, ["pranav", "meera", "kabir"]) for i in SHOPRITE_ITEMS]],
              "tax_cents": 89, "fees": [], "total_cents": 9518}},
    {"name": "three_way_remainder", "note": "$10.00 over 3: extra cent goes to lowest sort_order on tie",
     "bill": {"participants": P[:3], "items": [item("pizza", 1000, ["pranav", "arjun", "meera"])], "tax_cents": 0, "fees": [], "total_cents": 1000}},
    {"name": "weighted", "note": "v1.1 weights stored from day one: rice 2:1:1",
     "bill": {"participants": P[:3], "items": [item("rice", 1000, ["pranav", "arjun", "meera"], {"pranav": 2})], "tax_cents": 0, "fees": [], "total_cents": 1000}},
    {"name": "fees_tip_discount", "note": "Delivery fee, tip and an order-level coupon",
     "bill": {"participants": P, "items": [item("a", 2000, ["pranav", "arjun"]), item("b", 1001, ALL)],
              "tax_cents": 0, "fees": [{"label": "Delivery", "kind": "fee", "amount_cents": 599},
                                        {"label": "Tip", "kind": "tip", "amount_cents": 500},
                                        {"label": "Coupon", "kind": "order_discount", "amount_cents": 300}],
              "total_cents": 2000 + 1001 + 599 + 500 - 300}},
    {"name": "single_person", "note": "Everything is just the payer: nobody owes anything",
     "bill": {"participants": P, "items": [item("x", 1234, ["pranav"])], "tax_cents": 82, "fees": [], "total_cents": 1316}},
    {"name": "zero_and_negative_items", "note": "Free promo item and a bottle-deposit return",
     "bill": {"participants": P[:2], "items": [item("water", 599, ["pranav", "arjun"]), item("promo", 0, ["arjun"]),
                                                item("deposit_return", -60, ["pranav", "arjun"])],
              "tax_cents": 40, "fees": [], "total_cents": 599 - 60 + 40}},
    {"name": "all_zero_subtotals", "note": "Only a free item, but a bag fee: fee splits equally",
     "bill": {"participants": P[:3], "items": [item("freebie", 0, ["pranav", "arjun", "meera"])], "tax_cents": 0,
              "fees": [{"label": "Bag fee", "kind": "fee", "amount_cents": 10}], "total_cents": 10}},
]


def main() -> None:
    out = []
    for case in CASES:
        result = compute_shares(case["bill"])
        out.append({**case, "expected": result})
        print(f"{case['name']:40s} {result['shares']}")
    path = Path(__file__).resolve().parents[2] / "packages" / "fixtures" / "money_cases.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
