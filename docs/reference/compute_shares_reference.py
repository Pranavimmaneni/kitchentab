"""Reference implementation of KitchenTab's split math (PRD section 6).

This file is the executable spec. The production `apps/api/app/domain/money.py`
and `apps/web/lib/money/` must produce identical output for every case in
`packages/fixtures/money_cases.json`. Run this file to regenerate and verify
the fixtures:  python3 docs/reference/compute_shares_reference.py
Verify without rewriting (CI):  python3 docs/reference/compute_shares_reference.py --check

Rules encoded here:
- All money is integer cents. No floats touch money.
- Item line totals are split by weight using largest-remainder rounding.
- An allocation may instead carry a fixed share of its line (`share_num`/`share_den`,
  0 < num <= den; PRD 19.4 custom split, ADR-0030): fixed shares take that exact
  fraction, the weighted allocations split what is left by weight. If the fixed
  shares reach the whole line (or nobody is weighted), the fixed shares split the
  whole line in proportion to their fractions and weighted allocations get 0.
- Ties on remainder break by participant sort_order, then participant id.
- Bill-level amounts (tax, each fee/tip, each order discount) are allocated one
  at a time by each participant's item subtotal clipped at 0. If every clipped
  subtotal is 0, they are split equally among participants with any allocation.
- Negative amounts are rounded on their absolute value, then negated.
- A reconciliation override (PRD FR-13) stores the printed receipt total as the
  bill total and adds one signed `adjustment` bill-level amount for the
  difference, allocated like tax. Adjustments are stored signed; order
  discounts are stored positive and negated here.
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


def item_weights(alloc: list[dict]) -> dict[str, Fraction]:
    """Each allocation's exact fraction of its line (custom split, ADR-0030)."""
    fixed: dict[str, Fraction] = {}
    weighted: dict[str, Fraction] = {}
    for a in alloc:
        if a.get("share_num") is not None:
            num, den = a["share_num"], a["share_den"]
            if not 0 < num <= den:
                raise ValueError(f"share must be in (0, 1]: {num}/{den}")
            fixed[a["participant"]] = Fraction(num, den)
        else:
            weighted[a["participant"]] = Fraction(str(a.get("weight", 1)))
    if not fixed:
        return weighted
    rest = 1 - sum(fixed.values())
    out: dict[str, Fraction] = {}
    wsum = sum(weighted.values())
    for a in alloc:
        pid = a["participant"]
        if pid in fixed:
            out[pid] = fixed[pid]
        else:
            out[pid] = rest * weighted[pid] / wsum if rest > 0 else Fraction(0)
    return out


def compute_shares(bill: dict) -> dict:
    participants = bill["participants"]  # [{id, sort_order}]
    order = {p["id"]: (p["sort_order"], p["id"]) for p in participants}
    item_sub = {p["id"]: 0 for p in participants}
    allocated_any: set[str] = set()

    for item in bill["items"]:
        alloc = item["allocations"]  # [{participant, weight}]
        if not alloc:
            raise ValueError(f"item {item['id']} has no allocations")
        weights = item_weights(alloc)
        for pid, cents in largest_remainder(item["line_total_cents"], weights, order).items():
            item_sub[pid] += cents
        allocated_any.update(weights)

    shares = dict(item_sub)
    clipped = {pid: Fraction(max(v, 0)) for pid, v in item_sub.items() if pid in allocated_any}
    if sum(clipped.values()) == 0:
        clipped = {pid: Fraction(1) for pid in allocated_any}

    bill_level = [("tax", bill.get("tax_cents", 0))]
    for f in bill.get("fees", []):  # kind: fee | tip | order_discount | adjustment (signed)
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


def item(iid, cents, who, weights=None, shares=None):
    def alloc(w):
        if w in (shares or {}):
            return {"participant": w, "share_num": shares[w][0], "share_den": shares[w][1]}
        return {"participant": w, "weight": (weights or {}).get(w, 1)}
    return {"id": iid, "line_total_cents": cents, "allocations": [alloc(w) for w in who]}


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
    *[{"name": name, "note": note,
       "bill": {"participants": P, "items": [i if i["id"] != "chicken" else item("chicken", 1137, ["pranav", "arjun", "kabir"], shares={"kabir": share}) for i in
                                             [i if i["id"] != "coffee" else item("coffee", 999, ["pranav", "meera", "kabir"]) for i in SHOPRITE_ITEMS]],
                "tax_cents": 89, "fees": [], "total_cents": 9518}}
      for name, note, share in [
          ("request_custom_split_quarter", "Custom split (PRD 19.4, ADR-0030): Pranav approves Kabir's ask for chicken thighs at 25%: Kabir pays a quarter of the $11.37 line, Pranav and Arjun split the rest", (1, 4)),
          ("request_custom_split_amount", "Custom split by value: Pranav types $5.00 of the $11.37 line, stored as the exact fraction 500/1137 so Kabir's line share is exactly 500", (500, 1137)),
          ("request_custom_split_all", "Custom split at 100%: Kabir pays the whole chicken line; Pranav and Arjun stay ticked at $0.00", (1, 1)),
      ]],
    {"name": "custom_share_over_the_whole_line", "note": "Two fixed shares adding up past 100% (3/4 + 1/2): they split the line 3:2 and the weighted owner gets 0",
     "bill": {"participants": P[:3], "items": [item("cheese", 1001, ["pranav", "arjun", "meera"], shares={"arjun": (3, 4), "meera": (1, 2)})],
              "tax_cents": 50, "fees": [], "total_cents": 1051}},
    {"name": "custom_share_nobody_weighted", "note": "Only a 1/4 fixed share left on the line (the owners were unticked later): it takes the whole line",
     "bill": {"participants": P[:2], "items": [item("milk", 429, ["arjun"], shares={"arjun": (1, 4)}), item("eggs", 549, ["pranav"])],
              "tax_cents": 0, "fees": [], "total_cents": 978}},
    {"name": "custom_share_negative_line_and_weights", "note": "A 1/3 fixed share on a deposit return next to weighted 2:1 owners; rounded on the absolute value, then negated",
     "bill": {"participants": P[:3], "items": [item("deposit_return", -100, ["pranav", "arjun", "meera"], weights={"pranav": 2}, shares={"meera": (1, 3)}),
                                                item("water", 700, ["pranav", "arjun", "meera"])],
              "tax_cents": 0, "fees": [], "total_cents": 600}},
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
    {"name": "reconciliation_adjustment", "note": "FR-13 override: receipt prints $96.38 but items + tax = $95.18; payer uses the receipt total, so a +$1.20 adjustment is allocated like tax",
     "bill": {"participants": P, "items": SHOPRITE_ITEMS, "tax_cents": 89,
              "fees": [{"label": "Receipt adjustment", "kind": "adjustment", "amount_cents": 120}], "total_cents": 9638}},
    {"name": "reconciliation_adjustment_negative", "note": "FR-13 override with a printed total below the items: -$0.07 adjustment, rounded on its absolute value then negated",
     "bill": {"participants": P[:3], "items": [item("pizza", 1000, ["pranav", "arjun", "meera"])], "tax_cents": 0,
              "fees": [{"label": "Receipt adjustment", "kind": "adjustment", "amount_cents": -7}], "total_cents": 993}},
]


def main(argv: list[str]) -> int:
    out = []
    for case in CASES:
        result = compute_shares(case["bill"])
        out.append({**case, "expected": result})
        print(f"{case['name']:40s} {result['shares']}")
    path = Path(__file__).resolve().parents[2] / "packages" / "fixtures" / "money_cases.json"
    text = json.dumps(out, indent=2) + "\n"
    if "--check" in argv:
        current = path.read_bytes().decode("utf-8") if path.exists() else ""
        if current != text:
            print(f"FAIL: {path} is stale or not LF; run without --check to regenerate")
            return 1
        print(f"ok: {path} matches the reference")
        return 0
    # newline="\n" keeps the fixture LF on Windows as well.
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
