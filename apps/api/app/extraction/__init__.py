"""Local receipt extraction with no API key (BUILD_ALL C1, ADR-0010, ADR-0020).

pdf_text.extract_pdf_lines(bytes) -> PdfTextResult      text PDFs (needs_ocr for scans)
lines.lines_from_ocr(payload) / lines.merge_parts(parts)   browser OCR lines, multi-photo merge
parser.parse_receipt(lines, source=...) -> ExtractedBill  one parser for both inputs
"""
