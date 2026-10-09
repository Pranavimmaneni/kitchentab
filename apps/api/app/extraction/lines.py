"""Positioned text lines: the common input of the receipt parser (PDF text and browser OCR).

Coordinates are in the source's own units (PDF points or image pixels), origin top-left.
`confidence` is an integer in thousandths, 0..1000 (1000 for a PDF text layer; tesseract.js line
confidence x 10 for OCR). Coordinates may be fractional; confidences never are.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

Coord = float  # positions are PDF points or image pixels and may be fractional


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int = 1
    confidence: int = 1000  # thousandths


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    page: int = 1
    confidence: int = 1000  # thousandths
    seam: bool = False  # kept at a multi-photo seam where an overlapping duplicate was dropped
    words: tuple[Word, ...] = field(default=(), compare=False)

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 0.0)


def group_words(words: Iterable[Word], *, y_tolerance: float | None = None) -> list[Line]:
    """Group words into lines by vertical overlap, then sort each line left to right.

    Two words share a line when their vertical centers are within `y_tolerance` (default: 45% of
    the median word height). Wide horizontal gaps are kept as two spaces so a right-aligned price
    stays visibly separate from the name.
    """
    by_page: dict[int, list[Word]] = {}
    for w in words:
        if w.text.strip():
            by_page.setdefault(w.page, []).append(w)
    out: list[Line] = []
    for page in sorted(by_page):
        ws = sorted(by_page[page], key=lambda w: ((w.top + w.bottom) / 2, w.x0))
        heights = sorted(max(w.bottom - w.top, 0.1) for w in ws)
        tol = y_tolerance if y_tolerance is not None else max(heights[len(heights) // 2] * 0.45, 0.5)
        rows: list[list[Word]] = []
        centers: list[float] = []
        for w in ws:
            c = (w.top + w.bottom) / 2
            if rows and abs(c - centers[-1]) <= tol:
                rows[-1].append(w)
                n = len(rows[-1])
                centers[-1] = centers[-1] + (c - centers[-1]) / n
            else:
                rows.append([w])
                centers.append(c)
        for row in rows:
            out.append(_line_from_words(sorted(row, key=lambda w: w.x0)))
    return out


def _line_from_words(row: Sequence[Word]) -> Line:
    parts: list[str] = []
    char_w = sum((w.x1 - w.x0) / max(len(w.text), 1) for w in row) / len(row)
    prev: Word | None = None
    for w in row:
        if prev is not None:
            gap = w.x0 - prev.x1
            parts.append("  " if gap > char_w * 2.5 else " ")
        parts.append(w.text)
        prev = w
    conf = min(w.confidence for w in row)
    return Line(
        text="".join(parts),
        x0=min(w.x0 for w in row),
        x1=max(w.x1 for w in row),
        top=min(w.top for w in row),
        bottom=max(w.bottom for w in row),
        page=row[0].page,
        confidence=conf,
        words=tuple(row),
    )


def lines_from_ocr(payload: dict[str, Any]) -> list[Line]:
    """Browser OCR payload → lines. Shape (what the web OCR worker posts to `/bills/{id}/ocr`):

    {"pages": [{"page": 1, "width": 1200, "height": 3000,
                "lines": [{"text": "MILK 4.29", "bbox": [x0, y0, x1, y1], "confidence": 91.5,
                           "words": [{"text": "MILK", "bbox": [...], "confidence": 93.0}, ...]}]}]}

    Confidences are tesseract percentages (0..100); they are stored as integer thousandths.
    """
    out: list[Line] = []
    for p_index, page in enumerate(payload.get("pages", []), start=1):
        page_no = int(page.get("page", p_index))
        for raw in page.get("lines", []):
            text = str(raw.get("text", "")).replace("\n", " ").strip()
            if not text:
                continue
            x0, y0, x1, y1 = (float(v) for v in raw.get("bbox", (0, 0, 0, 0)))
            words = tuple(
                Word(
                    text=str(w.get("text", "")),
                    x0=float(w["bbox"][0]),
                    top=float(w["bbox"][1]),
                    x1=float(w["bbox"][2]),
                    bottom=float(w["bbox"][3]),
                    page=page_no,
                    confidence=_conf1000(w.get("confidence", 100)),
                )
                for w in raw.get("words", [])
                if str(w.get("text", "")).strip() and "bbox" in w
            )
            out.append(
                Line(
                    text=re.sub(r"\s+", " ", text),
                    x0=x0,
                    x1=x1,
                    top=y0,
                    bottom=y1,
                    page=page_no,
                    confidence=_conf1000(raw.get("confidence", 100)),
                    words=words,
                )
            )
    out.sort(key=lambda ln: (ln.page, ln.top, ln.x0))
    return out


def _conf1000(v: Any) -> int:
    """tesseract.js percent (0..100, may be fractional) → integer thousandths."""
    scaled = Decimal(str(v)) * 10
    return min(max(int(scaled.to_integral_value()), 0), 1000)


def lines_from_text(text: str, *, page: int = 1, confidence: int = 1000) -> list[Line]:
    """Plain text (one receipt line per text line) → lines with synthetic positions."""
    out = []
    for i, raw in enumerate(text.splitlines()):
        if raw.strip():
            out.append(
                Line(
                    text=raw.strip(),
                    x0=0,
                    x1=len(raw) * 6.0,
                    top=i * 12.0,
                    bottom=i * 12.0 + 10,
                    page=page,
                    confidence=confidence,
                )
            )
    return out


def char_width(line: Line) -> Coord:
    """Average character width of a line (for 'aligned' and 'wide gap' tests)."""
    return max((line.x1 - line.x0) / max(len(line.text), 1), 1.0)


def price_column(lines: Sequence[Line]) -> Coord | None:
    """The right edge most priced lines share (median of the right half of their x1)."""
    xs = sorted(ln.x1 for ln in lines if re.search(r"\d[.,]\d{2}-?\s*[A-Z]{0,2}$", ln.text))
    if len(xs) < 3:
        return None
    upper = xs[len(xs) // 2 :]
    return upper[len(upper) // 2]


# --- multi-photo merge (EXTRACT-08, EDGE-15-02) ---------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _same(a: Line, b: Line) -> bool:
    na, nb = _norm(a.text), _norm(b.text)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # OCR reads the same printed line slightly differently in two photos.
    return len(na) >= 6 and SequenceMatcher(None, na, nb).ratio() >= 0.9


@dataclass(slots=True)
class MergeResult:
    lines: list[Line]
    dropped: list[Line]
    warnings: list[str]


def merge_parts(parts: Sequence[Sequence[Line]], *, max_overlap: int = 12) -> MergeResult:
    """Join photos of one long receipt, top to bottom.

    For each seam, find the longest run where the last k lines of the photo above equal the first
    k lines of the photo below, drop the copy below and mark the kept lines `seam=True` (the parser
    lowers their confidence so the review screen flags them). Pages are renumbered by part.
    """
    merged: list[Line] = []
    dropped: list[Line] = []
    warnings: list[str] = []
    for index, part in enumerate(parts, start=1):
        lines = [replace(ln, page=index) for ln in sorted(part, key=lambda ln: (ln.page, ln.top, ln.x0))]
        if not merged:
            merged.extend(lines)
            continue
        best = 0
        for k in range(min(max_overlap, len(merged), len(lines)), 0, -1):
            if all(_same(merged[len(merged) - k + j], lines[j]) for j in range(k)):
                best = k
                break
        if best:
            for j in range(len(merged) - best, len(merged)):
                merged[j] = replace(merged[j], seam=True)
            dropped.extend(lines[:best])
            warnings.append(f"overlap_deduped: {best} line(s) repeated across photos {index - 1} and {index}")
        merged.extend(lines[best:])
    return MergeResult(lines=merged, dropped=dropped, warnings=warnings)
