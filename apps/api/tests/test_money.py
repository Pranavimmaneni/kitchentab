"""compute_shares (PRD §6) against the fixtures, the reference corpus and properties."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.domain.money import (
    Allocation,
    BillInput,
    Fee,
    FeeKind,
    InvariantError,
    Item,
    MoneyError,
    Participant,
    ReconciliationError,
    bill_from_dict,
    compute_shares,
    largest_remainder,
    weight_to_milli,
)
from app.settings import REPO_ROOT
from tests.harness.tags import fr, tid

FIXTURES = REPO_ROOT / "packages" / "fixtures"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


corpus = _load("money_corpus", FIXTURES / "money_corpus.py")
FEE_KINDS: tuple[FeeKind, ...] = ("fee", "tip", "order_discount", "adjustment")
CASES = json.loads((FIXTURES / "money_cases.json").read_text(encoding="utf-8"))


def _corpus_bill(raw: dict[str, Any]) -> BillInput:
    return BillInput(
        participants=tuple(Participant(p["id"], p["sort_order"]) for p in raw["participants"]),
        items=tuple(
            Item(
                i["id"],
                i["line_total_cents"],
                tuple(Allocation(a["participant"], a["weight_milli"]) for a in i["allocations"]),
            )
            for i in raw["items"]
        ),
        tax_cents=raw["tax_cents"],
        fees=tuple(Fee(f["kind"], f["label"], f["amount_cents"]) for f in raw["fees"]),
        total_cents=raw["total_cents"],
    )


@tid("MONEY-01")
@fr("FR-23")
@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_MONEY_01_fixtures_exact(case: dict[str, Any]) -> None:
    result = compute_shares(bill_from_dict(case["bill"]))
    assert result.shares == case["expected"]["shares"]
    assert result.item_subtotals == case["expected"]["item_subtotals"]


@tid("MONEY-03")
def test_MONEY_03_corpus_matches_reference_digest() -> None:
    """10,000 random bills (1-200 items, 1-20 people, weights, fees, discounts, negatives):
    invariant holds for each and the digest equals the reference's (and TS checks the same digest)."""
    expected = json.loads((FIXTURES / "money_property_digest.json").read_text(encoding="utf-8"))
    bills = corpus.generate(expected["seed"], expected["count"])
    results = []
    for raw in bills:
        result = compute_shares(_corpus_bill(raw))
        assert sum(result.shares.values()) == raw["total_cents"]
        results.append(result.shares)
    assert corpus.digest(iter(results)) == expected["sha256"]


participants_st = st.lists(st.integers(0, 3), min_size=1, max_size=20).map(
    lambda orders: tuple(Participant(f"p{i:02d}", o) for i, o in enumerate(orders))
)


@st.composite
def bills(draw: st.DrawFn) -> BillInput:
    people = draw(participants_st)
    ids = [p.id for p in people]
    items = []
    for k in range(draw(st.integers(1, 30))):
        who = draw(st.lists(st.sampled_from(ids), min_size=1, max_size=len(ids), unique=True))
        weights = [draw(st.sampled_from([1000, 1000, 1000, 500, 2000, 1, 4999])) for _ in who]
        cents = draw(st.integers(-5000, 100_000))
        items.append(Item(f"i{k}", cents, tuple(Allocation(w, x) for w, x in zip(who, weights, strict=True))))
    tax = draw(st.integers(0, 10_000))
    fees = []
    for f in range(draw(st.integers(0, 4))):
        kind: FeeKind = draw(st.sampled_from(FEE_KINDS))
        amount = draw(st.integers(-999, 999)) if kind == "adjustment" else draw(st.integers(0, 5000))
        fees.append(Fee(kind, f"f{f}", amount))
    total = (
        sum(i.line_total_cents for i in items)
        + tax
        + sum(-f.amount_cents if f.kind == "order_discount" else f.amount_cents for f in fees)
    )
    return BillInput(people, tuple(items), tax, tuple(fees), total)


@tid("MONEY-03")
@settings(max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(bill=bills())
def test_MONEY_03_invariant_property(bill: BillInput) -> None:
    result = compute_shares(bill)
    assert sum(result.shares.values()) == bill.total_cents
    # Equal weights: every item split differs by at most one cent.
    for item in bill.items:
        if len({a.weight_milli for a in item.allocations}) == 1:
            split = result.by_item[item.id].values()
            assert max(split) - min(split) <= 1


@tid("MONEY-04")
def test_MONEY_04_ties_break_by_sort_order_then_id() -> None:
    order = {"b": (0, "b"), "a": (1, "a"), "c": (1, "c")}
    # 100 over 3 equal weights: one leftover cent to the lowest sort_order ("b").
    assert largest_remainder(100, {"a": 1, "b": 1, "c": 1}, order) == {"a": 33, "b": 34, "c": 33}
    # 101 over 3: two leftover cents: "b" then the lower id among equal sort_order ("a").
    assert largest_remainder(101, {"a": 1, "b": 1, "c": 1}, order) == {"a": 34, "b": 34, "c": 33}
    # Negative amounts round on the absolute value, then negate.
    assert largest_remainder(-101, {"a": 1, "b": 1, "c": 1}, order) == {"a": -34, "b": -34, "c": -33}
    # Determinism: same input in any key order gives the same output.
    shuffled = {"c": 1, "a": 1, "b": 1}
    assert largest_remainder(101, shuffled, order) == largest_remainder(101, {"a": 1, "b": 1, "c": 1}, order)


@tid("MONEY-05")
def test_MONEY_05_fees_by_clipped_subtotal_and_all_zero_split_equally() -> None:
    by_name = {c["name"]: c for c in CASES}
    for name in (
        "fees_tip_discount",
        "all_zero_subtotals",
        "zero_and_negative_items",
        "reconciliation_adjustment",
    ):
        case = by_name[name]
        assert compute_shares(bill_from_dict(case["bill"])).shares == case["expected"]["shares"]
    # A participant whose items net negative gets no share of tax (clipped at 0).
    people = (Participant("a", 0), Participant("b", 1))
    bill = BillInput(
        people,
        (Item("x", 1000, (Allocation("a"),)), Item("deposit", -300, (Allocation("b"),))),
        tax_cents=70,
        fees=(),
        total_cents=770,
    )
    assert compute_shares(bill).shares == {"a": 1070, "b": -300}


@tid("MONEY-06")
def test_MONEY_06_no_float_touches_money() -> None:
    """Static scan: no float literal, float() call or float annotation in money code, and no true
    division on anything named like money. The TS side has the matching lint test."""
    violations: list[str] = []
    money_files = [REPO_ROOT / "apps" / "api" / "app" / "domain" / "money.py"]
    money_files += [
        p for p in (REPO_ROOT / "apps" / "api" / "app").rglob("*.py") if "cents" in p.read_text(encoding="utf-8")
    ]
    for path in sorted(set(money_files)):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            where = f"{path.name}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                violations.append(f"{where} float literal {node.value}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                violations.append(f"{where} float() call")
            elif isinstance(node, ast.Name) and node.id == "float" and isinstance(node.ctx, ast.Load):
                violations.append(f"{where} float annotation")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                if any("cents" in n or "amount" in n or "total" in n for n in names):
                    violations.append(f"{where} true division on money")
    allowed = {"clock.py", "logs.py"}  # timestamps, not money
    violations = [v for v in violations if v.split(":")[0] not in allowed]
    assert violations == []


@tid("MONEY-10")
def test_MONEY_10_limits_200_items_20_members_100k() -> None:
    people = tuple(Participant(f"p{i:02d}", i) for i in range(20))
    items = tuple(Item(f"i{k}", 50_000, tuple(Allocation(p.id) for p in people[: 1 + k % 20])) for k in range(200))
    total = sum(i.line_total_cents for i in items)
    assert total == 10_000_000  # $100,000.00
    bill = BillInput(people, items, tax_cents=0, fees=(), total_cents=total)
    started = time.perf_counter()
    result = compute_shares(bill)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert sum(result.shares.values()) == 10_000_000
    assert elapsed_ms < 50, f"{elapsed_ms:.1f} ms"


def test_errors_are_explicit() -> None:
    people = (Participant("a", 0),)
    with pytest.raises(ReconciliationError):
        compute_shares(BillInput(people, (Item("x", 100, (Allocation("a"),)),), 0, (), 101))
    with pytest.raises(MoneyError, match="no allocations"):
        compute_shares(BillInput(people, (Item("x", 100, ()),), 0, (), 100))
    with pytest.raises(MoneyError, match="unknown participant"):
        compute_shares(BillInput(people, (Item("x", 100, (Allocation("zz"),)),), 0, (), 100))
    with pytest.raises(MoneyError, match="twice"):
        compute_shares(BillInput(people, (Item("x", 100, (Allocation("a"), Allocation("a"))),), 0, (), 100))
    assert issubclass(InvariantError, MoneyError)


def test_edge_paths_for_full_branch_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    people = (Participant("a", 0),)
    # A bill with no items and nothing to allocate is all zeros.
    assert compute_shares(BillInput(people, (), 0, (), 0)).shares == {"a": 0}
    # Bill-level money with nobody allocated cannot be split.
    with pytest.raises(MoneyError, match="no allocated participants"):
        compute_shares(BillInput(people, (), 50, (), 50))
    with pytest.raises(MoneyError, match="weights must be positive"):
        largest_remainder(10, {"a": 0}, {"a": (0, "a")})
    for bad in ("0", "-1", "1.0005"):
        with pytest.raises(MoneyError, match="weight"):
            weight_to_milli(bad)
    assert weight_to_milli("2.5") == 2500


def test_invariant_guard_refuses_instead_of_adjusting(monkeypatch: pytest.MonkeyPatch) -> None:
    """§6 step 5: if the split ever loses a cent, refuse to save rather than patch it up."""
    import app.domain.money as money

    real = money.largest_remainder

    def lossy(amount: int, weights: Any, order: Any) -> dict[str, int]:
        out = real(amount, weights, order)
        if amount > 0 and out:
            first = next(iter(out))
            out[first] -= 1
        return out

    monkeypatch.setattr(money, "largest_remainder", lossy)
    bill = BillInput((Participant("a", 0),), (Item("x", 100, (Allocation("a"),)),), 0, (), 100)
    with pytest.raises(InvariantError):
        money.compute_shares(bill)


def test_inputs_must_be_integer_cents_and_known_fee_kinds() -> None:
    base = {
        "participants": [{"id": "a", "sort_order": 0}],
        "items": [{"id": "x", "line_total_cents": 100, "allocations": [{"participant": "a"}]}],
        "tax_cents": 0,
        "fees": [],
        "total_cents": 100,
    }
    for bad in ({"total_cents": 100.0}, {"tax_cents": 1.5}, {"total_cents": True}):
        with pytest.raises(MoneyError, match="integer"):
            bill_from_dict({**base, **bad})
    with pytest.raises(MoneyError, match="fee kind"):
        bill_from_dict({**base, "fees": [{"kind": "bribe", "label": "x", "amount_cents": 1}]})
    bad_fee = Fee("bribe", "x", 1)  # type: ignore[arg-type]
    with pytest.raises(MoneyError, match="fee kind"):
        compute_shares(BillInput((Participant("a", 0),), (Item("x", 100, (Allocation("a"),)),), 0, (bad_fee,), 101))


reference = _load("compute_shares_reference", REPO_ROOT / "docs" / "reference" / "compute_shares_reference.py")


@st.composite
def bills_with_shares(draw: st.DrawFn) -> dict[str, Any]:
    """The reference JSON shape, with fixed shares (custom split, ADR-0030) mixed into weighted lines."""
    people = [{"id": f"p{i:02d}", "sort_order": draw(st.integers(0, 3))} for i in range(draw(st.integers(1, 8)))]
    ids = [p["id"] for p in people]
    items: list[dict[str, Any]] = []
    for k in range(draw(st.integers(1, 12))):
        who = draw(st.lists(st.sampled_from(ids), min_size=1, max_size=len(ids), unique=True))
        allocs: list[dict[str, Any]] = []
        for pid in who:
            if draw(st.integers(0, 3)) == 0:
                den = draw(st.sampled_from([1, 3, 4, 100, 1137, 999_983, 10_000_000]))
                allocs.append({"participant": pid, "share_num": draw(st.integers(1, den)), "share_den": den})
            else:
                allocs.append({"participant": pid, "weight": draw(st.sampled_from(["1", "1", "0.5", "2", "0.001"]))})
        items.append({"id": f"i{k}", "line_total_cents": draw(st.integers(-5000, 10_000_000)), "allocations": allocs})
    tax = draw(st.integers(0, 5000))
    return {
        "participants": people,
        "items": items,
        "tax_cents": tax,
        "fees": [],
        "total_cents": sum(i["line_total_cents"] for i in items) + tax,
    }


@tid("MONEY-11")
@fr("FR-81")
@settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(raw=bills_with_shares())
def test_MONEY_11_fixed_shares_match_the_reference(raw: dict[str, Any]) -> None:
    """Custom split: integer common-denominator weights == the reference's exact Fractions, and a
    fixed share of a line gets exactly its fraction (to the cent) whenever someone weighted is left."""
    bill = bill_from_dict(raw)
    result = compute_shares(bill)
    expected = reference.compute_shares(raw)
    assert result.shares == expected["shares"]
    assert result.item_subtotals == expected["item_subtotals"]
    for item in bill.items:
        fixed = [a for a in item.allocations if a.share]
        if len(fixed) == 1 and len(item.allocations) > 1:
            num, den = fixed[0].share  # type: ignore[misc]
            got = abs(result.by_item[item.id][fixed[0].participant])
            assert abs(got * den - abs(item.line_total_cents) * num) < den, "within a cent of the fraction"


@tid("MONEY-11")
def test_MONEY_11_value_split_lands_on_the_exact_cent() -> None:
    """Owner types $5.00 of an $11.37 line: stored as 500/1137, the asker's line share is 500."""
    people = (Participant("pranav", 0), Participant("arjun", 1), Participant("kabir", 3))
    for total, value in [(1137, 500), (1137, 1), (1137, 1136), (99_999, 33_333), (10_000_000, 1)]:
        line = Item("x", total, (Allocation("pranav"), Allocation("arjun"), Allocation("kabir", share=(value, total))))
        result = compute_shares(BillInput(people, (line,), 0, (), total))
        assert result.by_item["x"]["kabir"] == value
    with pytest.raises(MoneyError, match="share"):
        compute_shares(BillInput(people, (Item("x", 10, (Allocation("kabir", share=(2, 1)),)),), 0, (), 10))
