# ADR-0010: Receipt extraction with no API key (BUILD_ALL C1)

Supersedes PRD §9's LLM-first pipeline. Default path, no keys:

1. Text PDFs: server-side text with line positions via `pdfplumber` (fallback `pypdf`).
2. Photos and scanned PDFs: OCR in the browser with `tesseract.js` (worker, core and `eng`
   traineddata self-hosted under `/ocr/`), PDF pages rendered with `pdfjs-dist`, preprocessing on
   device (EXIF rotate, grayscale, contrast, adaptive threshold, deskew, max 2000 px). The browser
   sends text lines with boxes and confidences.
3. One parser (`app/extraction/parser.py`) with a generic line-item grammar and store profiles
   emits the same `ExtractedBill` schema as the optional LLM plugin, with per-line confidence.
4. The server recomputes totals; mismatches show the reconciliation banner; review is mandatory.

The LLM plugin runs only when `EXTRACTOR_API_KEY` is set. Accuracy is measured on 30 generated
receipts (`packages/fixtures/receipts/`) by `make test-extract`. OCR runs in the browser so the
Python bundle stays small (Vercel 500 MB limit) and no image passes through a function body.
