"""Accuracy harness over the generated receipts (BUILD_ALL C1 targets, EXTRACT-03/16).

    cd apps/api && uv run python -m app.extraction.evaluate      (or scripts/receipts/evaluate.py)

Text PDFs go through `pdf_text` + `parser`; image fixtures use the tesseract.js output stored next
to them (`<id>.ocr.json`, made by scripts/receipts/ocr.mjs) + `parser` (multi-photo fixtures are
merged with `merge_parts` first). Ground truth is `<id>.truth.json`.

  line exactly right  normalized name (case-insensitive, letters and digits only) + line_total_cents
                      + quantity equal to a truth line, matched in order (LCS). The rate divides by
                      max(truth lines, extracted lines), so invented lines count against it.
  total right         total_cents equals the printed total in the truth
  mismatch flagged    every receipt whose truth has a reconciliation mismatch is flagged

All rates are integer basis points (MONEY-06: no floats in modules that touch cents).
Writes `REPORT.md` and `report.json` next to the fixtures; exits 1 if any target is missed.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.extraction.amounts import normalize_quantity
from app.extraction.lines import lines_from_ocr, merge_parts
from app.extraction.parser import parse_receipt
from app.extraction.pdf_text import extract_pdf_lines
from app.extraction.schema import ExtractedBill, ExtractedItem

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "packages" / "fixtures" / "receipts"

# Targets in percent (BUILD_ALL C1).
TEXT_LINES_PCT, TEXT_TOTALS_PCT = 98, 100
IMAGE_LINES_PCT, IMAGE_TOTALS_PCT = 85, 95


def norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def line_key(item: ExtractedItem) -> tuple[str, int, Decimal]:
    return (norm_name(item.name), item.line_total_cents, normalize_quantity(item.quantity))


def extract_fixture(fx: dict[str, Any], root: Path = FIXTURES) -> ExtractedBill:
    if fx["kind"] == "text_pdf":
        res = extract_pdf_lines((root / fx["files"][0]).read_bytes())
        if res.needs_ocr:
            return ExtractedBill(status="failed", warnings=["no_text_layer"]).recompute()
        return parse_receipt(res.lines, source="pdf_text", warnings=res.warnings)
    payload = json.loads((root / f"{fx['id']}.ocr.json").read_text(encoding="utf-8"))
    if payload.get("multi_photo"):
        parts = [lines_from_ocr({"pages": [p]}) for p in payload["pages"]]
        merged = merge_parts(parts)
        return parse_receipt(merged.lines, source="ocr", warnings=merged.warnings)
    return parse_receipt(lines_from_ocr(payload), source="ocr")


def _lcs_pairs(a: list[tuple[str, int, Decimal]], b: list[tuple[str, int, Decimal]]) -> list[tuple[int, int]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


@dataclass
class FixtureScore:
    id: str
    kind: str
    profile: str
    truth_lines: int
    extracted_lines: int
    lines_right: int
    total_right: bool
    expects_mismatch: bool
    flagged: bool
    status: str
    tax_right: bool
    fees_right: bool
    discounts_right: bool
    date_right: bool
    store_right: bool
    tags_right: int  # matched lines whose storage + category agree with the human label
    wrong_lines: list[str] = field(default_factory=list)
    extra_lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def score(fx: dict[str, Any], pred: ExtractedBill, truth: ExtractedBill) -> FixtureScore:
    tk = [line_key(i) for i in truth.items]
    pk = [line_key(i) for i in pred.items]
    pairs = _lcs_pairs(tk, pk)
    hit_t = {i for i, _ in pairs}
    hit_p = {j for _, j in pairs}
    fees = sorted((f.kind, f.amount_cents) for f in pred.fees)
    tfees = sorted((f.kind, f.amount_cents) for f in truth.fees)
    tags = sum(
        1
        for i, j in pairs
        if (truth.items[i].storage, truth.items[i].category) == (pred.items[j].storage, pred.items[j].category)
    )
    return FixtureScore(
        id=fx["id"],
        kind="text" if fx["kind"] == "text_pdf" else "image",
        profile=fx.get("profile", ""),
        truth_lines=len(tk),
        extracted_lines=len(pk),
        lines_right=len(pairs),
        total_right=pred.total_cents == truth.total_cents,
        expects_mismatch=truth.mismatch_cents is not None,
        flagged=pred.has_mismatch,
        status=pred.status,
        tax_right=(pred.tax_cents or 0) == (truth.tax_cents or 0),
        fees_right=fees == tfees,
        discounts_right=[i.discount_cents for i in pred.items if line_key(i) in tk]
        == [i.discount_cents for i in truth.items if line_key(i) in pk],
        date_right=pred.purchase_date == truth.purchase_date,
        store_right=pred.store_name == truth.store_name,
        tags_right=tags,
        wrong_lines=[f"{k[0]} {k[1]} x{k[2]}" for i, k in enumerate(tk) if i not in hit_t],
        extra_lines=[f"{k[0]} {k[1]} x{k[2]}" for j, k in enumerate(pk) if j not in hit_p],
        warnings=pred.warnings,
    )


def bp(num: int, den: int) -> int:
    return num * 10000 // den if den else 10000


def pct(bps: int) -> str:
    return f"{bps // 100}.{bps % 100:02d}%"


@dataclass
class Summary:
    scores: list[FixtureScore]
    groups: dict[str, dict[str, Any]]
    passed: bool


def evaluate(root: Path = FIXTURES) -> Summary:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    scores: list[FixtureScore] = []
    for fx in manifest["fixtures"]:
        truth = ExtractedBill.model_validate_json((root / f"{fx['id']}.truth.json").read_text(encoding="utf-8"))
        scores.append(score(fx, extract_fixture(fx, root), truth))
    groups: dict[str, dict[str, Any]] = {}
    passed = True
    for kind, lines_target, totals_target in (
        ("text", TEXT_LINES_PCT, TEXT_TOTALS_PCT),
        ("image", IMAGE_LINES_PCT, IMAGE_TOTALS_PCT),
    ):
        ss = [s for s in scores if s.kind == kind]
        right = sum(s.lines_right for s in ss)
        denom = sum(max(s.truth_lines, s.extracted_lines) for s in ss)
        totals = sum(1 for s in ss if s.total_right)
        expects = [s for s in ss if s.expects_mismatch]
        flagged = sum(1 for s in expects if s.flagged)
        lines_ok = right * 100 >= lines_target * denom
        totals_ok = totals * 100 >= totals_target * len(ss)
        flags_ok = flagged == len(expects)
        passed = passed and lines_ok and totals_ok and flags_ok
        groups[kind] = {
            "receipts": len(ss),
            "lines_right": right,
            "lines_denominator": denom,
            "lines_right_bp": bp(right, denom),
            "lines_target_pct": lines_target,
            "totals_right": totals,
            "totals_right_bp": bp(totals, len(ss)),
            "totals_target_pct": totals_target,
            "mismatches_expected": len(expects),
            "mismatches_flagged": flagged,
            "other_receipts_flagged": sum(1 for s in ss if s.flagged and not s.expects_mismatch),
            "tax_right": sum(1 for s in ss if s.tax_right),
            "fees_right": sum(1 for s in ss if s.fees_right),
            "dates_right": sum(1 for s in ss if s.date_right),
            "stores_right": sum(1 for s in ss if s.store_right),
            "tags_right_bp": bp(sum(s.tags_right for s in ss), right),
            "passed": lines_ok and totals_ok and flags_ok,
        }
    return Summary(scores=scores, groups=groups, passed=passed)


def render_markdown(summary: Summary) -> str:
    out = [
        "# Receipt extraction accuracy (EXTRACT-03, EXTRACT-16)",
        "",
        "Generated by `scripts/receipts/evaluate.py` over `packages/fixtures/receipts/` (ADR-0020).",
        "A line is right when its normalized name, line total and quantity equal the ground truth;",
        "the rate divides by max(truth lines, extracted lines) so invented lines count as misses.",
        "",
        "| Set | Receipts | Lines right | Target | Totals right | Target | Mismatches flagged | Result |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for kind, g in summary.groups.items():
        out.append(
            f"| {'Text PDFs' if kind == 'text' else 'Images / scanned PDFs'} | {g['receipts']} | "
            f"{pct(g['lines_right_bp'])} ({g['lines_right']}/{g['lines_denominator']}) | >= {g['lines_target_pct']}% | "
            f"{pct(g['totals_right_bp'])} ({g['totals_right']}/{g['receipts']}) | >= {g['totals_target_pct']}% | "
            f"{g['mismatches_flagged']}/{g['mismatches_expected']} | {'PASS' if g['passed'] else 'FAIL'} |"
        )
    out += [
        "",
        "Secondary (not targets): tax, fees, dates, store names per receipt; storage+category agreement on",
        "right lines.",
        "",
        "| Set | Tax | Fees | Dates | Stores | Tags | Other receipts flagged |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for kind, g in summary.groups.items():
        n = g["receipts"]
        out.append(
            f"| {kind} | {g['tax_right']}/{n} | {g['fees_right']}/{n} | {g['dates_right']}/{n} | "
            f"{g['stores_right']}/{n} | "
            f"{pct(g['tags_right_bp'])} | {g['other_receipts_flagged']} |"
        )
    out += [
        "",
        "## Per receipt",
        "",
        "| Id | Profile | Lines | Total | Mismatch flag | Status | Misses |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summary.scores:
        misses = "; ".join([*(f"-{w}" for w in s.wrong_lines), *(f"+{x}" for x in s.extra_lines)])[:300]
        flag = ("flagged" if s.flagged else "MISSED") if s.expects_mismatch else ("flagged" if s.flagged else "-")
        out.append(
            f"| {s.id} | {s.profile} | {s.lines_right}/{max(s.truth_lines, s.extracted_lines)} | "
            f"{'ok' if s.total_right else 'WRONG'} | {flag} | {s.status} | {misses or '-'} |"
        )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else FIXTURES
    summary = evaluate(root)
    (root / "REPORT.md").write_text(render_markdown(summary), encoding="utf-8")
    report = {
        "groups": summary.groups,
        "passed": summary.passed,
        "fixtures": [s.__dict__ for s in summary.scores],
    }
    (root / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for kind, g in summary.groups.items():
        print(
            f"{kind:5} lines {pct(g['lines_right_bp'])} (target {g['lines_target_pct']}%)  "
            f"totals {pct(g['totals_right_bp'])} (target {g['totals_target_pct']}%)  "
            f"mismatches flagged {g['mismatches_flagged']}/{g['mismatches_expected']}  "
            f"{'PASS' if g['passed'] else 'FAIL'}"
        )
    return 0 if summary.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
