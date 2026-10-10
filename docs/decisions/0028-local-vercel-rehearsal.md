# ADR-0028: The `TARGET=local-vercel` rehearsal and `make launch-check`

Status: accepted · P11 (BUILD_ALL §6 steps 1–2) · Refines ADR-0017

**No `vercel build` / `vercel dev`.** Both need a linked project and `vercel pull`, which the
rehearsal must never create (BUILD_ALL §3 rule 8). ADR-0017 allows the closest equivalent, so the
rehearsal runs the same entrypoints Vercel runs, unchanged:

| Vercel | Rehearsal (`make local-vercel-up`, `scripts/local_vercel.py`) |
| --- | --- |
| web service (`apps/web`, nextjs) | `next build` + `next start` (production build, no `KT_API_ORIGIN` proxy) on 127.0.0.1:3030 |
| api service (`app.vercel_entry:app`) | `uvicorn app.vercel_entry:app` on 127.0.0.1:8030, `PROFILE=vercel` |
| edge: `vercel.json` rewrites, HTTPS, Hobby limits | Caddy 2.10.2 (`lv-edge`, pinned by digest) on https://localhost:8443 |
| Neon pooled / direct URL | PgBouncer 1.24.1 (`lv-pgbouncer`, transaction pooling) :6432 / `lv-postgres` (16.15) :5442, database `kitchentab_lv` |
| Vercel Blob (separate HTTPS origin) | fake Blob service (`lv-fake-blob`) behind https://localhost:8444, its own generated read-write token |
| Vercel Queues + the `outbox-drain` subscriber | `python -m vercel.queue.devserver` :8031 + `scripts/local_vercel_drain.py` |
| a browser's push service | fake push service (`lv-fake-push`) :4020 |

Ports avoid 3000/8000 (a demo), 3010/8010 and 3020/8020 (Playwright suites); `LV_*_PORT` override them.

**Edge.** The Caddyfile has exactly `vercel.json`'s rewrites, in order (`/api/blob-upload`,
`/api/blob-view` → web; `/api/*` → api; everything else → web; full path kept), checked by a test
that parses both files (VERCEL-07). Request bodies with `Content-Length` over 4,500,000 bytes get
413 before any upstream sees them, chunked bodies are capped by `request_body max_size` (also 413);
the server's write timeout of 300 s is a deadline from the request headers to the end of the
response, i.e. Vercel's max duration (the SSE stream closes itself at 270 s). HTTP/1.1 and h2 only.
Caddy adds `Via: 1.1 Caddy`; nothing else is added. Not emulated: the 4.5 MB *response* limit and
the 2 GB memory limit.

**Its own Postgres.** At first the rehearsal used a `kitchentab_lv` database on the shared compose
Postgres; a 10-minute simulation then failed with `too many clients already` because parallel test
suites hold most of its 100 connections. `lv-postgres` isolates the rehearsal completely (and
`make local-vercel-down ARGS=--drop-db` removes its volume). PgBouncer runs with
`max_prepared_statements = 0`, stricter than Neon (which supports protocol-level prepared
statements), so the app is proven not to need a prepared statement to outlive its transaction;
`app/db.py`'s serverless engine (NullPool, `statement_cache_size=0`, unique statement names) ran
every API step of launch-check and a 10-minute household simulation through it without a database
error. The cost shows in latency: every query opens a connection, which is what makes realtime
checks with a 2 s budget (AUTH-09's revoked stream) tight in this profile. The pooled URL is a libpq URL as
Neon hands it out (`settings.asyncpg_url` converts it); Alembic gets the direct URL in the
SQLAlchemy form, as `make deploy` passes it.

**Queues.** The devserver is a broker only: it stores, leases and acknowledges messages but cannot
call a function (the hosted service pushes each message to the queue-triggered function compiled
from `app/queues/outbox.py`). So a separate process drives *the same subscriber object* with the
SDK's `poll_and_handle(drain_outbox)` against `VERCEL_QUEUE_BASE_URL`: receive, lease, call,
acknowledge, redeliver after `retry_after` on failure. The difference to production is polling
(0.25 s when idle) instead of push delivery. The API sends with `VERCEL_QUEUE_TOKEN=vc-dev-token`
(what `vercel dev` hands its services; it disables deployment pinning) and `VERCEL_REGION=iad1`.

**`APP_ENV=test`, not `staging`.** Deployed-mode validation (`settings.py`) can't pass locally:
it requires `STORAGE_PROVIDER=vercel_blob`, non-localhost origins, and (in the push route) real
push-service endpoints. So the API runs with `APP_ENV=test` and `TEST_ROUTES_ENABLED=false` (no
`/__test/*` routes), and `local-vercel-up` first validates the exact same environment with
`APP_ENV=staging`, `STORAGE_PROVIDER=vercel_blob` and an https `*.vercel.app` origin swapped in;
it refuses to start if that fails. What still differs from production: no
`Strict-Transport-Security` header, `/api/v1/docs` is served, the push-endpoint allow-list is off
(so the fake push service can subscribe), and the Turnstile test token would be accepted (phone
codes are off anyway). Secrets are real-looking and generated once like `make deploy-setup` does
(token_urlsafe, a fresh VAPID pair, an 86-char `TEST_INBOX_SECRET`), kept in
`.cache/local-vercel/state.json` (gitignored), never printed. Rate limits are production's.
Settings also reads the developer's `.env`; every provider is set explicitly so it can't leak in.

**Uploads.** With `STORAGE_PROVIDER=fakeblob` the API mints the client token itself (ticket method
`PUT`, ADR-0024), so the browser and launch-check upload straight to the Blob origin; the Next.js
`/api/blob-upload` route (`handleUpload`) is only exercised by `launch-check TARGET=prod`, whose
client implements `@vercel/blob` 2.8.1's protocol (`blob.generate-client-token`, then `PUT
https://vercel.com/api/blob/?pathname=` with `x-api-version: 12`) and is unverified until then.

**Web build.** `KT_DIST_DIR=test-results/next-lv` keeps the build away from the other suites'
`.next`; `next build` rewrites `tsconfig.json`'s include list for a custom distDir, so the script
restores it byte for byte. next.config.ts names the service worker after a custom distDir, so the
rehearsal serves `/sw-next-lv.js` where production serves `/sw.js` (launch-check reads the path
from the state file); the build also writes `apps/web/public/sw-next-lv.js`.

**Trusting the local CA.** Caddy's root is `.cache/local-vercel/caddy/caddy/pki/authorities/local/root.crt`.
launch-check and the API (`SSL_CERT_FILE`, for its calls to the Blob origin) trust it directly;
Playwright's Node side via `NODE_EXTRA_CA_CERTS` (the Makefile sets it for `TARGET=local-vercel`);
Chromium via `--ignore-certificate-errors-spki-list` with the leaf, intermediate and root key
hashes, which makes the chain valid for the browser (secure context, service worker). WebKit and
Firefox can't be given a CA without changing the OS trust store, so their projects set
`ignoreHTTPSErrors` for `TARGET=local-vercel` only. A person's browser or phone: import `root.crt`
as a trusted authority (iPhone: AirDrop/mail it, install the profile, then Settings › General ›
About › Certificate Trust Settings; Android: Settings › Security › Install a certificate › CA;
Firefox: Settings › Certificates › Authorities), and for a phone start the rehearsal with
`LOCAL_VERCEL_HOST=<the PC's LAN IP>` so the certificate and the origin carry that address
(Windows will ask to allow ports 8443/8444 through the firewall). Use a separate browser profile:
`localhost` cookies are shared across ports with the http://localhost:3000 dev app.

**Processes.** The four host processes start detached (`--skip-build` / `LV_SKIP_BUILD=1` reuses the
last web build) (own process group, out of the caller's job
object) with logs in `.cache/local-vercel/logs/`; their PIDs are kept in the state file and a PID
is only killed if its command line is still ours (PIDs get reused).

**launch-check.** `scripts/launch_check.py` (stdlib + httpx) walks the product over HTTPS through
the edge in BUILD_ALL §6.2's order and prints PASS/FAIL/SKIP/MANUAL with timings; a step whose
prerequisite failed is skipped, and any FAIL or SKIP exits 1. Every user is
`<name>+lc<run>@launch-check.kitchentab.test` (`deploylib.TEST_EMAIL_DOMAIN`). The SSE step holds
the stream for `--sse-seconds` (default 285: the server rolls over at 270 s) while another member
writes every 15 s, writes while disconnected, reconnects with `Last-Event-ID` and compares every
received seq with the JSON catch-up (no gap, no duplicate, nothing missed); shorter waits reconnect
from the client instead. `TARGET=prod` never runs by default: it needs `--base-url`,
`LAUNCH_CHECK_URL` or the URL `make deploy` saved, and `TEST_INBOX_SECRET` (else it pulls it with
the Vercel CLI); push is a manual step there. The browser-only checks are
`apps/web/tests/e2e/launch/*.spec.ts` (`@launch`, Chromium): installability via CDP
(`Page.getInstallabilityErrors`), an active service worker for `/`, and one photo read by the
in-browser OCR from the capture screen with no third-party requests.
