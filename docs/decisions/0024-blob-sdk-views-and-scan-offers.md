# ADR-0024: Vercel Blob through the official SDKs, signed views, and stored scan offers

Status: accepted · Milestone 5 integration · Amends ADR-0015

1. **Server-side Blob calls use Vercel's Python SDK** (`vercel` package, `vercel.blob.AsyncBlobClient`)
   with `BLOB_READ_WRITE_TOKEN`: `head`, `get(access="private", use_cache=False)`, `put(access="private",
   overwrite=True)` for the daily export, and `delete` for retention. This replaces hand-written REST
   calls that were built from memory and could not be checked against the real service. `vercel` is
   Vercel's own SDK (the umbrella for the pre-approved `vercel-queue`); it adds no OCR or image code
   to the API bundle (VERCEL-08 still passes).
2. **Views.** Private stores have no public URLs, and signed URLs (`issueSignedToken` + `presignUrl`,
   up to 7 days) exist only in `@vercel/blob` (JS). `GET /api/v1/receipts/{id}/view` checks policy
   (house members only) and returns a same-origin link `/api/blob-view?pathname=…&expires=…&sig=…`.
   `sig` is HMAC-SHA256 over `"GET\n<pathname>\n<expires>"` keyed with the read-write token
   (`storage.sign_read`), valid for 5 minutes. The Next.js route at `/api/blob-view` (routed to the web
   service in `vercel.json`, ahead of `/api/(.*)`) recomputes the HMAC, rejects expired or wrong
   signatures with 403, mints a Vercel signed URL valid for 5 minutes and redirects to it. The file
   is then served from `*.blob.vercel-storage.com`, a separate origin (SEC-04).
3. **Uploads** stay as in ADR-0015: `/api/blob-upload` (Next.js, `handleUpload`) asks the API to
   authorize the pathname, then mints the client token. Locally the fake Blob service lets the API
   mint the client token itself with the same algorithm as the JS SDK, so tests need no Node route.
4. **Scan offers (SYNC-15).** A scan that finishes after the payer typed or edited items is not
   applied; its `ExtractedBill` stays on its `receipt_extractions` row with status `pending` and the
   bill view shows `scanned_offer`. Accepting it (`POST /bills/{id}/extractions/{eid}/apply`) replaces
   the items in one house write; dismissing or a newer scan marks it `failed` with error `dismissed`
   or `superseded`. No new status value was added, to avoid a migration while other work changed the
   schema; `pending` + the offer query is unambiguous because applied scans are `done`.

Unverified until the morning deploy: the exact JS signatures of `issueSignedToken`/`presignUrl` in the
installed `@vercel/blob` version (the web route's unit test pins them against the package), and the
Python SDK against a real store (`launch-check TARGET=prod` uploads, views and deletes a receipt).
