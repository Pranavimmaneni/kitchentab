# ADR-0026: Receipt capture and OCR in the browser

Status: accepted · Milestone 5 (web) · Builds on ADR-0010, ADR-0015, ADR-0024

1. **Self-hosted OCR assets.** `scripts/copy-ocr-assets.mjs` (run by `pnpm dev` / `pnpm build`)
   copies tesseract.js 5.1.1's `worker.min.js`, the two LSTM-only cores
   (`tesseract-core{-simd,}-lstm.wasm.js`) and pdfjs-dist 4.10.38's `pdf.min.mjs` +
   `pdf.worker.min.mjs` into `public/ocr/` (gitignored, versions follow the lockfile). The `eng`
   4.0.0_best_int model (`public/ocr/lang/eng.traineddata.gz`, 2.9 MB, MIT via
   `@tesseract.js-data/eng`) is **committed**: it is data, the accuracy report was measured with
   exactly this file, and committing it avoids a new dependency. The tesseract worker is loaded
   from its own URL (`workerBlobURL: false`) and pdfjs with a runtime `import()` of
   `/ocr/pdf.min.mjs`, so no request leaves the origin (EXTRACT-12). Verified under the nonce CSP
   from middleware.ts (`'strict-dynamic' 'wasm-unsafe-eval'`, `worker-src 'self' blob:`) on WebKit,
   Chromium and Firefox: photo OCR, scanned-PDF rendering, text-PDF upload and the service worker
   raise no CSP violation. The offline page is rendered per request (not static) so its scripts
   carry the nonce of the response the worker precaches.
2. **Same pipeline as the fixtures.** `lib/receipts/preprocess.ts` is a typed copy of
   `scripts/receipts/preprocess.js`; OCR runs PSM 6, `preserve_interword_spaces=1`, 300 dpi, one
   part per photo, all PDF pages in one part, exactly as `scripts/receipts/ocr.mjs` wrote the
   `*.ocr.json` fixtures.
3. **EXIF never leaves the device** by re-encoding every photo through a canvas (JPEG 0.9, PNG
   stays PNG, long side ≤ 4096 px) after decoding with EXIF rotation applied; a byte-level strip
   (`lib/receipts/exif.ts`) runs on the result as a guard. Re-encoding loses a little quality but
   also handles PNG/WEBP metadata and HEIC in one path. HEIC is decoded by the browser (Safari can;
   elsewhere the photo is refused with "This browser can't open that photo…"), so no HEIC library.
4. **On-device refusals first** (type by magic bytes, 20 MB, 10 PDF pages via pdfjs, photos or one
   PDF): nothing is created or uploaded for a refused pick. The server repeats every check.
5. **One scan job per draft in memory** (`lib/receipts/scan-job.ts`): the capture sheet creates
   the draft, starts the job and goes to the review screen, which shows the reading and failure
   states from it. Steps are resumable: Try again reuses prepared files, stored keys and the attach
   Idempotency-Key, so a retry never uploads or attaches twice (EXTRACT-06). After a reload the
   job is gone; a draft left `pending` or `failed` with no items shows the failure state with
   "Choose the file again". A text PDF that the server could not parse is offered to on-device
   OCR on Try again.
6. **Review form follows the server** for fields not typed locally (dirty-field tracking), so a
   scan or another device landing while the screen is open is never overwritten by a stale save.
7. **`X-Client-Version` is an integer** (`CLIENT_VERSION = "1"`): the API compares it numerically
   with `MIN_CLIENT_VERSION`, so the old `web/0.1.0` would have been answered 426 by any non-zero
   minimum.
