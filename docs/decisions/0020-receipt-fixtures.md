# ADR-0020: Generated receipt fixtures, Pillow as a dev dependency, and how accuracy is measured

Context: BUILD_ALL C1 / ADR-0010 need 30 receipts with known ground truth (15 text PDFs, 15 noisy
images or scanned PDFs) that anyone can regenerate, plus an accuracy report (EXTRACT-03, -16).

Decision:

- **Generator** `scripts/receipts/generate.py` (seed `20261002`) builds receipts from a product
  catalog (`catalog.py`) for the 8 store profiles (Instacart, Walmart, Target, Costco, Amazon/Whole
  Foods, ShopRite, Trader Joe's, H Mart) with the edge cases (multi-page, coupons and `/item no`
  coupons, weighted and multi-quantity lines above or below the item, deposits, bag/delivery/
  service fees, tips, refunds, wrapped long names, $0.00 items, order-level discounts, and two
  deliberate printed-total mismatches: r09 +$1.00, r15 -$0.50). Ground truth is computed in integer
  cents and written as `<id>.truth.json` in the `ExtractedBill` shape. Truth names are what a
  person reads on the receipt (thermal shorthand such as "ORG BNNA" is truth "Organic bananas"),
  never tuned to the parser.
- **Rendering**: HTML receipts are rendered by Playwright's Chromium (`render.mjs`, reusing
  `@playwright/test` from `apps/web`, browsers in `.cache/ms-playwright`): `page.pdf()` for text
  PDFs, full-page screenshots at 2.2x for the photo bases.
- **Noise** (`noise.py`) uses **Pillow, added to the API's `dev` dependency group only**: thermal
  fade with streaks, mesh warp (crumple), table background, keystone perspective, rotation ±7°,
  shadow gradient, Gaussian blur, sensor grain, JPEG quality 35-72; 4 are saved as image-only PDFs
  (one 2-page), 2 as multi-photo sets with 3 overlapping lines. The API code never imports Pillow.
  Note: `pdfplumber` (a runtime dependency per C1) itself depends on Pillow and `pypdfium2`, so
  Pillow is present in the deployed bundle transitively (about 15 MB total, far under the 500 MB
  Vercel limit); we do not use it at runtime.
- **OCR outputs** `<id>.ocr.json` are produced by `scripts/receipts/ocr.mjs`: tesseract.js 5.1.1
  (LSTM, `eng` 4.0.0_best_int, PSM 6, `preserve_interword_spaces=1`), installed into the throwaway
  `.cache/ocr-tools` (`npm i --prefix .cache/ocr-tools tesseract.js@5 @tesseract.js-data/eng@1
  pdfjs-dist@4`) so no package.json changes. Preprocessing is `scripts/receipts/preprocess.js`
  run inside Chromium on a canvas, exactly what the web OCR worker should run: EXIF orientation,
  max 2000 px, grayscale, 1-99% contrast stretch, projection-profile deskew (±10°), Bradley
  adaptive threshold (window = max side / 40, t = 0.15, minimum contrast 14). PSM 4 was tried and
  rejected: it drops the right-hand price column on receipts.
- **Measurement** lives in `apps/api/app/extraction/evaluate.py` (also run by pytest EXTRACT-03
  and by `scripts/receipts/evaluate.py`, which writes `REPORT.md` / `report.json`). A line is right
  when normalized name + line total + quantity match the truth (in-order LCS); the rate divides by
  max(truth lines, extracted lines) so invented lines count against it. Rates are integer basis
  points (MONEY-06).

Regenerate: `cd apps/api && uv run python ../../scripts/receipts/generate.py` then
`node scripts/receipts/ocr.mjs`, then `uv run python ../../scripts/receipts/evaluate.py`. The
generator is deterministic (truth, images and manifest hash identically across runs); text PDFs
differ only in their embedded timestamp. Fixtures total about 6 MB.

Consequences: accuracy numbers come from synthetic receipts rendered with system fonts; real
camera photos (perspective, glare, curled paper, low light) are harder, so the browser QA in
EXTRACT-12 and real-receipt spot checks remain necessary.
