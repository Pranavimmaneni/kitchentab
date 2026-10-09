"""Text-layer PDFs → positioned lines (BUILD_ALL C1 step 1, EXTRACT-11).

`pdfplumber` keeps word positions; if it fails on a file, `pypdf` gives text without positions
(synthetic ones are assigned in reading order). A PDF without a usable text layer (a scan) is
reported as `needs_ocr=True` so the caller asks the browser to OCR it.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from app.extraction.lines import Line, Word, group_words, lines_from_text

log = logging.getLogger(__name__)

MAX_PAGES = 10
MAX_BYTES = 20 * 1024 * 1024
# Fewer alphanumeric characters than this per page means "no real text layer".
MIN_TEXT_CHARS_PER_PAGE = 20


class PdfRejected(Exception):
    """The file can't be processed at all (not a PDF, encrypted, too many pages, too big)."""

    def __init__(self, reason: Literal["not_pdf", "encrypted", "too_many_pages", "too_large", "unreadable"]) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class PdfTextResult:
    lines: list[Line] = field(default_factory=list)
    page_count: int = 0
    needs_ocr: bool = False  # no text layer: render pages in the browser and OCR them
    method: Literal["pdfplumber", "pypdf", "none"] = "none"
    warnings: list[str] = field(default_factory=list)


def _check_header(data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise PdfRejected("too_large")
    if not data[:1024].lstrip().startswith(b"%PDF-") and b"%PDF-" not in data[:1024]:
        raise PdfRejected("not_pdf")


def _inspect(data: bytes) -> int:
    """Page count via pypdf; raises PdfRejected for encrypted or broken files."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PdfRejected("encrypted")
        return len(reader.pages)
    except PdfRejected:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        log.info("pypdf could not open pdf: %s", type(exc).__name__)
        raise PdfRejected("unreadable") from exc


def _alnum(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]", text))


def _with_pdfplumber(data: bytes) -> list[Line]:
    import pdfplumber

    words: list[Word] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for w in page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False, use_text_flow=False):
                words.append(
                    Word(
                        text=str(w["text"]),
                        x0=float(w["x0"]),
                        x1=float(w["x1"]),
                        top=float(w["top"]),
                        bottom=float(w["bottom"]),
                        page=page_no,
                    )
                )
    return group_words(words)


def _with_pypdf(data: bytes) -> list[Line]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    out: list[Line] = []
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout")
        except Exception:  # pypdf's layout mode is strict on some files
            text = page.extract_text()
        out.extend(lines_from_text(text or "", page=page_no))
    return out


def extract_pdf_lines(data: bytes) -> PdfTextResult:
    """Extract lines from a text PDF. Raises PdfRejected for files that can't be used at all."""
    _check_header(data)
    pages = _inspect(data)
    if pages > MAX_PAGES:
        raise PdfRejected("too_many_pages")
    result = PdfTextResult(page_count=pages)
    try:
        result.lines = _with_pdfplumber(data)
        result.method = "pdfplumber"
    except Exception as exc:  # any pdfminer failure: fall back rather than fail the upload
        log.info("pdfplumber failed, falling back to pypdf: %s", type(exc).__name__)
        result.warnings.append("pdfplumber_failed")
        try:
            result.lines = _with_pypdf(data)
            result.method = "pypdf"
        except Exception as exc2:
            raise PdfRejected("unreadable") from exc2
    chars = sum(_alnum(ln.text) for ln in result.lines)
    if chars < MIN_TEXT_CHARS_PER_PAGE * max(pages, 1) // 2 or chars < MIN_TEXT_CHARS_PER_PAGE:
        result.needs_ocr = True
        result.lines = []
        result.warnings.append("no_text_layer")
    return result
