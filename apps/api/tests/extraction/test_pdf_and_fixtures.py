"""Text PDFs, store profiles, accuracy targets and mismatch flags over the generated receipts."""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import pytest
from pypdf import PdfWriter

from app.extraction import pdf_text
from app.extraction.evaluate import FIXTURES, evaluate, extract_fixture, line_key, score
from app.extraction.parser import parse_receipt
from app.extraction.pdf_text import PdfRejected, extract_pdf_lines
from tests.extraction.conftest import manifest, truth
from tests.harness.tags import fr, tid

TEXT = [fx for fx in manifest() if fx["kind"] == "text_pdf"]
IMAGES = [fx for fx in manifest() if fx["kind"] != "text_pdf"]


def _blank_pdf(pages: int, password: str | None = None) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@tid("EXTRACT-11")
@fr("FR-11")
def test_EXTRACT_11_text_pdf_lines_keep_positions_and_pages() -> None:
    res = extract_pdf_lines((FIXTURES / "r06.pdf").read_bytes())
    assert res.method == "pdfplumber"
    assert res.page_count == 2 and not res.needs_ocr
    assert {ln.page for ln in res.lines} == {1, 2}
    priced = [ln for ln in res.lines if ln.text.endswith("$4.29")]
    assert priced and all(ln.x1 > ln.x0 > 0 and ln.bottom > ln.top for ln in priced)
    # reading order inside a page is top to bottom
    page1 = [ln.top for ln in res.lines if ln.page == 1]
    assert page1 == sorted(page1)


@tid("EXTRACT-11")
@fr("FR-11")
def test_EXTRACT_11_pypdf_fallback_still_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(data: bytes) -> Any:
        raise ValueError("pdfminer choked")

    monkeypatch.setattr(pdf_text, "_with_pdfplumber", broken)
    res = extract_pdf_lines((FIXTURES / "r01.pdf").read_bytes())
    assert res.method == "pypdf" and "pdfplumber_failed" in res.warnings
    bill = parse_receipt(res.lines)
    t = truth("r01")
    assert bill.total_cents == t.total_cents
    assert [line_key(i) for i in bill.items] == [line_key(i) for i in t.items]


@tid("EXTRACT-11")
@fr("FR-10")
def test_EXTRACT_11_scanned_pdf_reports_needs_ocr() -> None:
    scanned = next(fx for fx in IMAGES if fx["kind"] == "scanned_pdf")
    res = extract_pdf_lines((FIXTURES / scanned["files"][0]).read_bytes())
    assert res.needs_ocr and res.lines == [] and "no_text_layer" in res.warnings


@tid("EXTRACT-11")
@fr("FR-10")
@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (_blank_pdf(1, password="secret"), "encrypted"),
        (_blank_pdf(11), "too_many_pages"),
        (b"\x89PNG\r\n\x1a\n not a pdf", "not_pdf"),
        (b"%PDF-1.7\n garbage", "unreadable"),
    ],
)
def test_EXTRACT_11_rejects_unusable_pdfs(data: bytes, reason: str) -> None:
    with pytest.raises(PdfRejected) as err:
        extract_pdf_lines(data)
    assert err.value.reason == reason


@tid("EXTRACT-11")
def test_EXTRACT_11_blank_ten_page_pdf_needs_ocr() -> None:
    res = extract_pdf_lines(_blank_pdf(10))
    assert res.page_count == 10 and res.needs_ocr


@tid("EXTRACT-13")
@fr("FR-11")
@pytest.mark.parametrize("fx", TEXT, ids=[f"{fx['id']}-{fx['profile']}" for fx in TEXT])
def test_EXTRACT_13_store_profiles_parse_per_ground_truth(fx: dict[str, Any]) -> None:
    bill = extract_fixture(fx)
    t = truth(fx["id"])
    s = score(fx, bill, t)
    assert s.wrong_lines == [] and s.extra_lines == []
    assert bill.total_cents == t.total_cents
    assert (bill.tax_cents or 0) == (t.tax_cents or 0)  # no TAX line printed means no tax
    assert bill.subtotal_cents == t.subtotal_cents
    assert sorted((f.kind, f.amount_cents) for f in bill.fees) == sorted((f.kind, f.amount_cents) for f in t.fees)
    assert [i.discount_cents for i in bill.items] == [i.discount_cents for i in t.items]
    assert bill.store_name == t.store_name and bill.purchase_date == t.purchase_date
    for got, want in zip(bill.items, t.items, strict=True):
        assert got.raw_text  # raw text kept next to the expanded name
        if want.unit_price_cents and want.quantity != 1:
            assert got.unit_price_cents == want.unit_price_cents


@tid("EXTRACT-13")
@fr("FR-11")
def test_EXTRACT_13_edge_cases_in_the_generated_set() -> None:
    """The set really contains each edge case the C1 brief lists, and each parses right."""
    seen: set[str] = set()
    for fx in TEXT:
        t = truth(fx["id"])
        bill = extract_fixture(fx)
        assert [line_key(i) for i in bill.items] == [line_key(i) for i in t.items]
        if any(i.discount_cents for i in t.items):
            seen.add("coupons")
        if any(i.quantity != i.quantity.to_integral_value() for i in t.items):
            seen.add("weighted")
        if any(f.label == "Bottle deposit" for f in t.fees):
            seen.add("deposits")
        if any(i.line_total_cents < 0 for i in t.items):
            seen.add("refunds")
        if any(i.line_total_cents == 0 for i in t.items):
            seen.add("zero")
        if any(f.kind == "tip" for f in t.fees):
            seen.add("tip")
        if any(f.kind == "order_discount" for f in t.fees):
            seen.add("order_discount")
        if any(len(i.name) > 40 for i in t.items):
            seen.add("wrapped")
        if extract_pdf_lines((FIXTURES / fx["files"][0]).read_bytes()).page_count > 1:
            seen.add("multipage")
    assert seen >= {
        "coupons",
        "weighted",
        "deposits",
        "refunds",
        "zero",
        "tip",
        "order_discount",
        "wrapped",
        "multipage",
    }
    profiles = {fx["profile"] for fx in TEXT}
    assert profiles == {"instacart", "walmart", "target", "costco", "amazon", "shoprite", "traderjoes", "hmart"}


@tid("EXTRACT-13")
def test_EXTRACT_13_abbreviations_expand_but_raw_text_is_kept() -> None:
    bill = extract_fixture(next(fx for fx in TEXT if fx["id"] == "r01"))
    bananas = next(i for i in bill.items if "banana" in i.name.lower())
    assert bananas.name == "Organic bananas"
    assert "ORG BNNA" in bananas.raw_text
    assert bananas.quantity == Decimal("2.1") and bananas.unit_price_cents == 63 and bananas.line_total_cents == 132


@tid("EXTRACT-03")
@fr("FR-11")
def test_EXTRACT_03_accuracy_targets_on_the_30_receipts() -> None:
    summary = evaluate()
    assert len(TEXT) >= 15 and len(IMAGES) >= 15
    text, image = summary.groups["text"], summary.groups["image"]
    assert text["lines_right"] * 100 >= 98 * text["lines_denominator"], text
    assert text["totals_right"] == text["receipts"], text
    assert image["lines_right"] * 100 >= 85 * image["lines_denominator"], image
    assert image["totals_right"] * 100 >= 95 * image["receipts"], image
    assert summary.passed


@tid("EXTRACT-16")
@fr("FR-13")
@pytest.mark.parametrize("fx", manifest(), ids=[fx["id"] for fx in manifest()])
def test_EXTRACT_16_every_mismatch_is_flagged(fx: dict[str, Any]) -> None:
    bill = extract_fixture(fx)
    t = truth(fx["id"])
    # The review banner shows exactly when printed and recomputed totals differ (or no total was read).
    assert bill.has_mismatch == (bill.total_cents is None or bill.total_cents != bill.computed_total_cents)
    if t.mismatch_cents is not None:
        assert bill.has_mismatch, fx["id"]
        assert any(w.startswith("total_mismatch") for w in bill.warnings)
    assert fx["expects_mismatch"] == (t.mismatch_cents is not None)


@tid("EXTRACT-16")
def test_EXTRACT_16_generated_set_contains_deliberate_mismatches() -> None:
    expected = [fx["id"] for fx in manifest() if fx["expects_mismatch"]]
    assert any(fx.startswith("r") for fx in expected) and any(fx.startswith("i") for fx in expected)
