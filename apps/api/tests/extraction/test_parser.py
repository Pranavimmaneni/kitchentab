"""Parser grammar on small synthetic receipts: money, quantities, coupons, fees, confidence,
classification, multi-photo merge and failure behaviour. No database, no app.main."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.extraction.amounts import parse_cents, repair_amount
from app.extraction.classify import classify
from app.extraction.lines import Line, lines_from_ocr, lines_from_text, merge_parts
from app.extraction.parser import parse_receipt, parse_text
from app.extraction.schema import ExtractedBill, ExtractedFee, ExtractedItem
from tests.harness.tags import fr, tid


def _items(bill: ExtractedBill) -> list[tuple[str, Decimal, int | None, int, int]]:
    return [(i.name, i.quantity, i.unit_price_cents, i.line_total_cents, i.discount_cents) for i in bill.items]


# --- money ---------------------------------------------------------------------------------------


@tid("EXTRACT-11")
@fr("FR-11")
@pytest.mark.parametrize(
    ("token", "cents"),
    [("4.29", 429), ("$4.29", 429), ("1,234.50", 123450), ("-$1.00", -100), ("1.00-", -100), ("(2.50)", -250),
     (".63", 63), ("$0.00", 0), ("4.2", None), ("429", None), ("abc", None)],
)  # fmt: skip
def test_EXTRACT_11_money_is_parsed_with_integer_arithmetic(token: str, cents: int | None) -> None:
    assert parse_cents(token) == cents


@tid("EXTRACT-11")
@pytest.mark.parametrize(
    ("token", "cents", "max_cert"),
    [("4.29", 429, 1000), ("4.Z9", 429, 850), ("S4.29", 429, 850), ("l.29", 129, 850), ("O.99", 99, 850),
     ("4,29", 429, 850), ("3.00-", -300, 1000)],
)  # fmt: skip
def test_EXTRACT_11_ocr_noise_in_prices(token: str, cents: int | None, max_cert: int) -> None:
    got = repair_amount(token)
    assert got is not None and got[0] == cents and got[1] <= max_cert


@tid("EXTRACT-11")
def test_EXTRACT_11_missing_decimal_point_only_when_allowed() -> None:
    assert repair_amount("429") is None
    got = repair_amount("429", allow_missing_point=True)
    assert got == (429, 600)


# --- server arithmetic -----------------------------------------------------------------------------


@tid("EXTRACT-04", "EDGE-15-03")
@fr("FR-13")
def test_EXTRACT_04_server_recomputes_and_never_trusts_printed_totals() -> None:
    bill = parse_text(
        """SHOPRITE
        MILK 4.29
        EGGS 5.49
        SUBTOTAL 9.78
        TAX 0.50
        TOTAL 12.00"""
    )
    assert bill.computed_total_cents == 429 + 549 + 50
    assert bill.total_cents == 1200
    assert bill.mismatch_cents == 1200 - 1028
    assert any(w.startswith("total_mismatch") for w in bill.warnings) and bill.has_mismatch


@tid("EXTRACT-04")
@fr("FR-13")
def test_EXTRACT_04_recompute_ignores_model_arithmetic() -> None:
    """An LLM-shaped payload with a wrong computed total is corrected by recompute()."""
    bill = ExtractedBill(
        items=[ExtractedItem(name="Milk", line_total_cents=429, discount_cents=29)],
        tax_cents=10,
        fees=[
            ExtractedFee(kind="tip", label="Tip", amount_cents=100),
            ExtractedFee(kind="order_discount", label="Promo", amount_cents=50),
        ],
        total_cents=460,
        computed_total_cents=999_999,
    ).recompute()
    assert bill.computed_total_cents == 400 + 10 + 100 - 50
    assert bill.mismatch_cents is None and not bill.has_mismatch


@tid("EXTRACT-04")
def test_EXTRACT_04_schema_rejects_float_money() -> None:
    with pytest.raises(ValidationError):
        ExtractedItem(name="Milk", line_total_cents=4.29)
    with pytest.raises(ValidationError):
        ExtractedItem(name="Milk", line_total_cents=429, confidence_x1000=1001)


# --- quantities (EDGE-15-04) --------------------------------------------------------------------------


@tid("EDGE-15-04")
@fr("FR-11")
def test_EDGE_15_04_quantity_and_weighted_lines() -> None:
    bill = parse_text(
        """TRADER JOE'S
        LIMES 3 @ $0.50 1.50
        BANANAS 1.32
          2.10 lb @ $0.63/lb
          2 x 1.29
        AVOCADO 2.58
        TORTILLAS QTY 2 4.38
        SUBTOTAL 9.78
        TOTAL 9.78"""
    )
    assert _items(bill) == [
        ("Limes", Decimal(3), 50, 150, 0),
        ("Bananas", Decimal("2.1"), 63, 132, 0),
        ("Avocado", Decimal(2), 129, 258, 0),
        ("Tortillas", Decimal(2), None, 438, 0),
    ]


@tid("EDGE-15-04")
def test_EDGE_15_04_quantity_line_above_the_item_and_walmart_forms() -> None:
    above = parse_text(
        """SHOPRITE
          3 @ 1.29
        LIMES 3.87 F
          2.10 lb @ .63 /lb
        ORG BNNA 1.32 F
        TOTAL 5.19"""
    )
    assert _items(above) == [("Limes", Decimal(3), 129, 387, 0), ("Organic bananas", Decimal("2.1"), 63, 132, 0)]
    walmart = parse_text(
        """Walmart
        GV BLACK BEANS 15OZ 007874235186 F 2.94 N
          3 AT 1 FOR 0.98
        BANANAS 000000004011 KF 1.32 N
          2.10 lb @ 1 lb /0.63
        TOTAL 4.26"""
    )
    assert _items(walmart) == [
        ("Great Value black beans 15 oz", Decimal(3), 98, 294, 0),
        ("Bananas", Decimal("2.1"), 63, 132, 0),
    ]


# --- coupons (EDGE-15-05) -----------------------------------------------------------------------------


@tid("EDGE-15-05")
@fr("FR-11")
def test_EDGE_15_05_coupon_attaches_to_the_item_above() -> None:
    bill = parse_text(
        """SHOPRITE
        GRK YGRT PLN 32OZ 6.79 F
          PRICE PLUS SAVINGS 1.00-
        WHOLE MILK 1 GAL 4.29 F
          MFR COUPON 0.50
        SUBTOTAL 9.58
        TOTAL 9.58"""
    )
    assert [(i.name, i.discount_cents) for i in bill.items] == [
        ("Greek yogurt plain 32 oz", 100),
        ("Whole milk 1 gal", 50),
    ]
    assert not any(f.kind == "order_discount" for f in bill.fees)
    assert bill.computed_total_cents == 958 and not bill.has_mismatch


@tid("EDGE-15-05")
def test_EDGE_15_05_unclear_discounts_become_order_level() -> None:
    bill = parse_text(
        """COSTCO
        E 1080215 KS ORG EGGS 24CT 7.99
        E 1234567 KS RSTD ALMONDS 16OZ 13.49
        321012 /1080215 3.00-
        STORE COUPON $5 OFF ORDER 5.00-
        BAG FEE 0.10
        CANDLE 2.00
        BIG COUPON 9.00-
        SUBTOTAL 6.59
        PROMOTION -1.00
        TOTAL 5.59"""
    )
    eggs = bill.items[0]
    assert eggs.discount_cents == 300  # Costco "/<item number>" reference finds its item
    assert bill.items[1].discount_cents == 0
    order = sorted(f.amount_cents for f in bill.fees if f.kind == "order_discount")
    assert order == [100, 500, 900]  # "ORDER", bigger than the item above, and after SUBTOTAL


# --- fees (EDGE-15-06) ------------------------------------------------------------------------------


@tid("EDGE-15-06")
@fr("FR-11")
def test_EDGE_15_06_deposits_bags_tips_delivery_service_fees() -> None:
    bill = parse_text(
        """instacart
        Sparkling Water Lime, 12 pk $10.98
        Bottle deposit $0.60
        Cola, 2 Liter Bottle $2.49
        Bottle deposit $0.10
        Items subtotal $13.47
        Checkout bag fee $0.30
        Delivery fee $3.99
        Service fee $2.15
        Tax $0.89
        Shopper tip $6.00
        Total $27.50"""
    )
    fees = sorted((f.kind, f.label, f.amount_cents) for f in bill.fees)
    assert fees == [
        ("fee", "Bottle deposit", 70),
        ("fee", "Checkout bag fee", 30),
        ("fee", "Delivery fee", 399),
        ("fee", "Service fee", 215),
        ("tip", "Shopper tip", 600),
    ]
    assert [i.name for i in bill.items] == ["Sparkling Water Lime, 12 pk", "Cola, 2 Liter Bottle"]
    assert bill.tax_cents == 89 and bill.total_cents == 2750 and not bill.has_mismatch


@tid("EXTRACT-13")
def test_EXTRACT_13_refunds_zero_items_and_wrapped_names() -> None:
    bill = parse_text(
        """H MART
        CJ BIBIGO PORK & VEGETABLE MINI
        WONTONS 24OZ 8.99
        CILANTRO 0.00
        RTN MANDARIN ORANGE CHKN -4.99
        TOTAL 4.00"""
    )
    assert _items(bill) == [
        ("Cj bibigo pork & vegetable mini wontons 24 oz", Decimal(1), 899, 899, 0),
        ("Cilantro", Decimal(1), 0, 0, 0),
        ("Mandarin orange chicken", Decimal(1), 499, -499, 0),
    ]
    assert not bill.has_mismatch


@tid("EXTRACT-13")
def test_EXTRACT_13_header_lines_are_not_glued_to_the_first_item() -> None:
    bill = parse_text(
        """TRADER JOE'S
        Store #615 - (201) 555-0172
        GROCERY
        BANANAS 0.58
        Balance to pay $0.58"""
    )
    assert [i.name for i in bill.items] == ["Bananas"]
    assert bill.store_name == "Trader Joe's"


# --- confidence (EXTRACT-05) ---------------------------------------------------------------------------


def _ocr(lines: list[tuple[str, int]]) -> list[Line]:
    payload = {
        "pages": [
            {
                "page": 1,
                "lines": [
                    {"text": t, "bbox": [10, 40 * k, 400, 40 * k + 30], "confidence": c}
                    for k, (t, c) in enumerate(lines)
                ],
            }
        ]
    }
    return lines_from_ocr(payload)


@tid("EXTRACT-05")
@fr("FR-12")
def test_EXTRACT_05_low_confidence_rows_are_flagged() -> None:
    bill = parse_receipt(
        _ocr(
            [
                ("SHOPRITE", 95),
                ("WHOLE MILK 4.29", 96),
                ("LG EGGS 5.4g", 92),
                ("BBY SPINACH 3.99", 61),
                ("TOTAL 13.77", 95),
            ]
        ),
        source="ocr",
    )
    by_name = {i.name: i for i in bill.items}
    assert not by_name["Whole milk"].flagged and by_name["Whole milk"].confidence_x1000 >= 800
    assert (
        by_name["Large eggs"].line_total_cents == 549 and by_name["Large eggs"].flagged
    )  # OCR repair lowers certainty
    assert by_name["Baby spinach"].flagged and by_name["Baby spinach"].confidence_x1000 < 800
    assert all(isinstance(i.confidence_x1000, int) for i in bill.items)


@tid("EXTRACT-05")
def test_EXTRACT_05_flag_threshold_is_0_8() -> None:
    assert ExtractedItem(name="x", line_total_cents=1, confidence_x1000=799).flagged
    assert not ExtractedItem(name="x", line_total_cents=1, confidence_x1000=800).flagged


# --- classification (EXTRACT-07) ----------------------------------------------------------------------


@tid("EXTRACT-07")
@fr("FR-60")
@pytest.mark.parametrize(
    ("name", "storage", "category", "icon", "shelf"),
    [
        ("Whole milk 1 gal", "fridge", "dairy", "milk", 7),
        ("Oat milk 64 oz", "fridge", "drinks", "plant_milk", 7),
        ("Large eggs 18 ct", "fridge", "eggs", "eggs", 28),
        ("Boneless chicken thighs", "fridge", "meat_fish", "chicken", 2),
        ("Frozen pork dumplings", "freezer", "frozen", "dumplings", 90),
        ("Vanilla ice cream", "freezer", "frozen", "ice_cream", 90),
        ("Basmati rice 10 lb", "pantry", "grains", "rice", 365),
        ("Organic bananas", "pantry", "produce", "banana", 6),
        ("Coffee beans 12 oz", "pantry", "coffee_tea", "coffee", 180),
        ("Creamy peanut butter", "pantry", "condiments", "sauce_jar", 180),
        ("Honey nut cereal", "pantry", "grains", "cereal", 365),
        ("Napa kimchi 32 oz", "fridge", "prepared", "kimchi_jar", 60),
        ("Paper towels 6 pk", "none", "household", "generic_pantry", None),
        ("Dish soap", "none", "household", "generic_pantry", None),
        ("Hass avocado", "pantry", "produce", "generic_pantry", 5),
        ("Mystery item", "pantry", "other", "generic_pantry", 30),
    ],
)
def test_EXTRACT_07_storage_category_icon_shelf_life(
    name: str, storage: str, category: str, icon: str, shelf: int | None
) -> None:
    c = classify(name)
    assert (c.storage, c.category, c.icon_key, c.shelf_life_days) == (storage, category, icon, shelf)


@tid("EXTRACT-07")
@fr("FR-60")
def test_EXTRACT_07_household_always_maps_to_none() -> None:
    item = ExtractedItem(name="Sponges", line_total_cents=299, category="household", storage="fridge")
    assert item.storage == "none"
    bill = parse_text("TARGET\nSPONGES 3PK T $2.99\nTOTAL $2.99")
    assert (bill.items[0].storage, bill.items[0].shelf_life_days) == ("none", None)


# --- multi-photo (EXTRACT-08, EDGE-15-02) ------------------------------------------------------------------


@tid("EXTRACT-08", "EDGE-15-02")
@fr("FR-11")
def test_EXTRACT_08_overlapping_photos_are_deduped_and_flagged() -> None:
    top = lines_from_text("COSTCO\nMILK 4.29\nEGGS 5.49\nBREAD 3.49")
    bottom = lines_from_text("EGGS 5.49\nBREAD 3.49\nRICE 14.99\nTOTAL 28.26")
    merged = merge_parts([top, bottom])
    assert [ln.text for ln in merged.lines] == [
        "COSTCO",
        "MILK 4.29",
        "EGGS 5.49",
        "BREAD 3.49",
        "RICE 14.99",
        "TOTAL 28.26",
    ]
    assert len(merged.dropped) == 2 and merged.warnings
    bill = parse_receipt(merged.lines, source="ocr", warnings=merged.warnings)
    names = [i.name for i in bill.items]
    assert names == ["Milk", "Eggs", "Bread", "Rice"]
    flagged = {i.name for i in bill.items if i.flagged}
    assert flagged == {"Eggs", "Bread"}  # the kept seam lines need a human look
    assert not bill.has_mismatch


@tid("EXTRACT-08")
def test_EXTRACT_08_ocr_variants_of_the_same_line_still_dedupe() -> None:
    top = lines_from_text("A 1.00\nKS ORGANIC EGGS 24CT 7.99")
    bottom = lines_from_text("KS 0RGANIC EGGS 24CT 7.99\nB 2.00")
    merged = merge_parts([top, bottom])
    assert [ln.text for ln in merged.lines] == ["A 1.00", "KS ORGANIC EGGS 24CT 7.99", "B 2.00"]
    no_overlap = merge_parts([lines_from_text("A 1.00"), lines_from_text("B 2.00")])
    assert not no_overlap.dropped and not no_overlap.warnings


@tid("EXTRACT-08")
def test_EXTRACT_08_multi_photo_fixture_has_no_duplicates(fixtures_by_id: dict[str, dict[str, object]]) -> None:
    from app.extraction.evaluate import extract_fixture
    from tests.extraction.conftest import truth

    multi = [fx for fx in fixtures_by_id.values() if fx.get("multi_photo")]
    assert multi
    for fx in multi:
        bill = extract_fixture(fx)
        t = truth(str(fx["id"]))
        assert len(bill.items) == len(t.items)
        assert any(w.startswith("overlap_deduped") for w in bill.warnings)


# --- failure (EDGE-15-01) ------------------------------------------------------------------------------


@tid("EDGE-15-01")
@fr("FR-11")
def test_EDGE_15_01_blurry_or_not_a_receipt_never_invents_items() -> None:
    blurry = parse_receipt(_ocr([("ee ae 4.29", 22), ("~ . ,", 8), ("Mlk 3.9g", 31), ("TOTAI 8.1", 25)]), source="ocr")
    assert blurry.items == [] and blurry.status == "failed"
    assert any("unreadable" in w or "not_a_receipt" in w for w in blurry.warnings)
    letter = parse_text("Dear roommates,\nPlease clean the kitchen tonight.\nThanks!\nPranav")
    assert letter.items == [] and letter.status == "failed" and letter.total_cents is None
    empty = parse_receipt([], source="ocr")
    assert empty.status == "failed" and empty.items == []


@tid("EDGE-15-01")
def test_EDGE_15_01_cut_off_receipt_is_low_confidence() -> None:
    bill = parse_text("WALMART\nGV WHOLE MILK 1 GAL 007874235186 F 3.48 N\nGV LG EGGS 18CT 007874235111 F 4.12 N")
    assert len(bill.items) == 2 and bill.total_cents is None
    assert bill.status == "low_confidence" and bill.has_mismatch and "no_total_found" in bill.warnings
