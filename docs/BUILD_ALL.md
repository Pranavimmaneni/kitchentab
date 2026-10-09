# BUILD_ALL — finish KitchenTab end to end, unattended (v2)

You are taking KitchenTab from whatever state this repo is in now to a fully working, fully tested
app, **built for Vercel and verified locally**, that Pranav deploys to Vercel tomorrow morning with
one command. **Do not deploy, log in to Vercel, or create any cloud resources tonight.** Pranav is asleep. Work
autonomously until §9 "Done" is true. This file overrides "stop for review" and "ask before"
instructions in KICKOFF_PROMPT.md and CLAUDE.md: do not stop between milestones.

**Work is already in progress.** You (or an earlier session) have already started building from the
kickoff prompt. Do not start over and do not redo finished work. Reconcile first (§2 P0), then
continue from the first unfinished item.

Read first, every time you resume: `CLAUDE.md`, `docs/PROGRESS.md`, this file, then the PRD and
TEST_PLAN sections for the milestone in progress. `docs/PROGRESS.md` is your checkpoint: after any
compaction, restart, or usage-limit pause, re-read it and continue from the first unchecked item.

---

## 1. Changes to the spec since the handoff (these win over PRD and TEST_PLAN)

### C1. No LLM API key; receipt extraction runs on free, local tools
- Extraction must work with **no API key at all**. The LLM extractor stays as an optional plugin,
  enabled only when `EXTRACTOR_API_KEY` is set. It is off by default and never required by any test
  except its own fake-backed tests.
- New extraction pipeline, one shared parser for every input:
  1. **Text PDFs** (e-receipts from Instacart, Walmart, Target, Costco, Amazon/Whole Foods, etc.):
     extract text server-side with `pdfplumber` (fallback `pypdf`), keeping line positions.
  2. **Scanned PDFs and photos:** OCR **in the browser** with `tesseract.js` (WASM; self-host the
     worker, core and `eng` traineddata under `/ocr/`, no third-party CDN). Render PDF pages with
     `pdfjs-dist`. Pre-process on device: EXIF rotate, grayscale, contrast, adaptive threshold,
     deskew, max 2000 px. Send the OCR text with line boxes and confidences to the API.
  3. **Receipt parser** (`apps/api/app/extraction/parser.py`): a generic line-item parser (name,
     qty × unit price, line total, discounts/coupons as negative lines, deposit/bag fees, tax,
     tip/delivery/service fees, subtotal, total, store name, date) plus small store profiles for
     the stores above. Output the same `ExtractedBill` schema the LLM path uses, with a per-line
     confidence.
  4. **Reconciliation:** if Σ lines + tax + fees ≠ printed total, show the existing mismatch
     banner on the review screen and highlight low-confidence lines. The review screen is always
     shown before a bill can be sent; manual entry is always available.
- **PDF upload is a first-class input** everywhere a photo is accepted (Scan screen: "Upload PDF"
  next to camera and photo library; also drag-and-drop on desktop). Multi-page PDFs supported.
  Limits: 20 MB, 10 pages; files are uploaded directly from the browser to storage (see C2), never
  through a function body.
- **Tests:** build `packages/fixtures/receipts/` with at least 30 synthetic receipts with known
  ground truth: 15 text PDFs across the store profiles and edge cases (multi-page, coupons, weighted
  items, deposits, refunds, long names wrapping, $0 items), and 15 images/scanned PDFs rendered from
  them with realistic noise (rotation ±7°, blur, shadow, thermal fade, crumple). Generate them with
  a script so they are reproducible. Targets, measured by `make test-extract` and reported in
  TEST_REPORT: text PDFs ≥ 98% of lines exactly right and 100% of totals right; images ≥ 85% of
  lines right and 95% of totals right; every receipt's review screen correctly flags any mismatch.
  Run browser OCR in Playwright on WebKit and Chromium for at least 5 of the image receipts.
- Update the EXTRACT tests in TEST_PLAN to this pipeline (keep IDs; add new IDs for PDF and OCR).

### C2. Deploy on Vercel (one project, one domain); nothing runs on Pranav's PC
Use **Vercel Services** (beta, all plans): one Vercel project, one domain
`https://<project>.vercel.app`, with the Next.js app at `/` and the FastAPI app at `/api` via
top-level rewrites in `vercel.json`. Same origin means first-party cookies; keep the CSP and CSRF
rules from the PRD. Target the free **Hobby** plan and its limits:

| PRD component | On Vercel |
| --- | --- |
| Postgres 16 | **Neon** from the Vercel Marketplace (`vercel integration add neon`), pooled URL for requests, direct URL for migrations. Run Alembic migrations in the build/deploy step, never from a request. |
| MinIO / S3 receipt storage | **Vercel Blob** with client uploads (browser → Blob with a short-lived token minted by the API after a policy check). Keep the storage interface; MinIO stays for local tests. |
| arq workers + Redis queues | **Vercel Queues** (beta, Python SDK `vercel-queue`) consumed by a function in the API service, with delayed delivery for quiet-hours and reminders. Keep the outbox: the request transaction writes outbox rows; after commit, publish a queue message to drain them; a sweeper endpoint drains leftovers. |
| Redis pub/sub for SSE | Postgres: the SSE endpoint polls `house_events` with `seq > last` every 750 ms (indexed), sends heartbeats, and **closes cleanly at 270 s** (Hobby max duration is 300 s); the client reconnects with `Last-Event-ID` as already specified. No Redis required in production. |
| Rate limits, idempotency store | Postgres tables (no Redis in production). |
| Scheduled jobs | Hobby cron runs **at most once a day** with ±59 min precision: use one daily cron for use-soon scans, retention and the daily export; everything time-sensitive uses delayed queue messages. |
| Backups | Neon's built-in restore plus a daily logical export to Blob; `make restore-drill` proves a restore into a fresh Neon branch or local Postgres. |

- Hard limits to respect and test: request/response body ≤ 4.5 MB, function duration ≤ 300 s,
  2 GB memory, Python bundle ≤ 500 MB (keep heavy deps out of the API; OCR is in the browser).
- Keep `docker compose` for local dev and the full test suite. Add a **`PROFILE=vercel`** mode
  that runs the same app code with the production adapters (Postgres-only realtime, queue
  adapter, Blob adapter with a local fake) so `make test-sync PROFILE=vercel` and
  `make e2e-multi PROFILE=vercel` exercise the code paths production uses.
- Record each change above as an ADR, update PRD §12/§13/§18 and the deploy docs to match.

### C3. Sign-in on day one
- **Email codes** are the day-one sign-in, sent through Gmail SMTP (`EMAIL_PROVIDER=smtp`,
  `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`).
  Pranav adds the Gmail address and app password in the morning; this is the **only** thing you
  may leave untested against the real service.
- Until then, production runs with `EMAIL_PROVIDER=testinbox`: codes are stored in a test inbox
  readable only at `/api/test/inbox` with header `X-Test-Secret: $TEST_INBOX_SECRET` (64 random
  chars, set via `vercel env`). Use it for local-vercel tests now and production checks in the morning. `make set-email` (below) turns it
  off.
- Phone codes (Twilio) and Google sign-in need accounts Pranav doesn't have yet: keep them
  implemented and tested with fakes, and **hide their buttons unless their keys are set**.

---

## 2. Phase plan

| Phase | Scope | Gate |
| --- | --- | --- |
| P0 Reconcile | Read `git log --oneline -50`, `docs/PROGRESS.md`, ADRs and the code; mark what is truly done (tests passing, not just written). Apply §1 to the PRD, TEST_PLAN, PROGRESS and ADRs. Retrofit §1 into already-built milestones (adapters, PDF upload, extraction). | `make lint test` green; PROGRESS.md accurate |
| P1–P9 | Remaining milestones from PRD §17, in order (M6 sync before any feature that writes) | Per-milestone gate below |
| P10 Full verification | §4 | all of §4 green |
| P11 Deploy-ready | §6 | `make launch-check TARGET=local-vercel` green; deploy scripts dry-run green |
| P12 Final | §9 Done | all true |

Keep working on the current branch. If you are on `main` with no branch yet, create `build/all`.

**Per-milestone gate** (all must pass before the next milestone):

1. `make lint test test-sync e2e e2e-multi` green, plus the same sync and multi-user suites with
   `PROFILE=vercel` (add `test-push` from M9 and `test-extract` from M5).
2. Every FR and TEST_PLAN ID in scope has a test; `scripts/check_traceability.py` green for done
   milestones.
3. **Fresh-eyes review:** spawn a reviewer subagent that has not seen your work. Give it the PRD
   sections, TEST_PLAN sections, §1 of this file and the diff, and ask it to find spec gaps, bugs,
   race conditions, security holes, Vercel-limit violations and mismatches with
   `docs/design/renders/`. Fix every confirmed finding, then rerun the gate.
4. Screens in scope match `docs/design/renders/*` at 390 px in light and dark.
5. PROGRESS.md updated; commit.

## 3. Operating rules

1. **Never stop to ask.** Decide, record an ADR and a line under "Assumptions" in PROGRESS.md,
   keep going. Only the items in §8 may wait for Pranav, and they never block other work.
2. **Commit small and often** with FR and TEST_PLAN IDs in the message. Never force-push, never
   rewrite history, never commit secrets (`.env*`, `.vercel/`, tokens). Push only to a remote that
   is already configured.
3. **Keep PROGRESS.md current** after every task, written so a fresh session can resume from it.
4. **Root causes only.** Never weaken an assertion, add sleeps, raise timeouts, skip or delete
   tests to get green. If a test is truly wrong, fix it and record why in an ADR.
5. **Follow CLAUDE.md non-negotiables** (cents, policy module, one-transaction writes with the
   house event sequence, the four write kinds, outbox, fakes in tests). New dependencies named in
   §1 are pre-approved; for others, choose the smallest well-maintained option and record an ADR.
6. **Context hygiene:** short tool output (`-q`, tail, grep failures); subagents for wide searches,
   reviews and test-writing fan-outs; main session orchestrates.
7. **If something loops:** after 3 failed fixes for the same failure, write a root-cause note,
   isolate it with a minimal failing test, try a different approach. Never spend more than ~90
   minutes on one bug without moving on and coming back.
8. **No cloud tonight:** never run `vercel login`, `vercel link`, `vercel deploy`,
   `vercel integration`, or `vercel env` against the real account, and never create cloud
   resources. Everything is verified locally; Pranav deploys in the morning.

## 4. P10 full verification (no stone unturned)

1. **Coverage:** every TEST_PLAN ID (as updated by §1), every FR, every row of PRD §15, §19.9 and
   §21.7 has a test. `check_traceability.py` reports 100%.
2. **Run everything locally:** `make test-all` (unit, integration, SYNC ×200 in both profiles,
   Playwright on WebKit iPhone + Chromium Pixel + Firefox, E2E-01..10, extraction accuracy,
   push via the fake push service, chaos via Toxiproxy, k6 load, security incl. authorization
   matrix and dependency audits, axe both themes, visual diffs, Lighthouse + PWA audit, migrations
   up/down, backup-restore drill, retention, invariant checker).
3. **Live household simulation:** `scripts/simulate_household.py --profile vercel` seeds a house
   with 4 members on 6 devices (3 browser contexts + 3 API clients) and for 30 minutes runs random
   realistic actions concurrently: PDF and photo bills, manual bills, ticks/unticks, price edits,
   sends, payments (double taps, lost responses), settles, reopens, asks answered every way, used
   up, sharing switches, member removal and rejoin, network drops, tab sleeps, server restarts and
   SSE stream rollovers at 270 s. Every 30 s and at the end assert: every device equals a fresh GET,
   Σ shares = total on every bill, balances match the ledger, inventory owners match allocations,
   event sequences gapless with no duplicates, every notification matches a committed event and
   no duplicate reached a device. Run 3 seeds. Fix every failure at the root with a regression test.
4. **Exploratory pass:** one subagent per charter in TEST_PLAN §6 tries to break the app through
   the UI and API and returns repro steps. Fix, add regression tests.
5. **Three clean runs:** `make test-all` and the simulation three times in a row, no failures.
6. **Report:** fill `docs/TEST_REPORT.md` (TEST_PLAN §8 template, plus extraction accuracy and the
   local-vercel results from §6) and write `docs/DEVICE_QA.md` (real-phone checklist from TEST_PLAN
   §5, written for the production URL Pranav gets in the morning, with test accounts).

Use a workflow for the P10 fan-out: one agent per TEST_PLAN area, plus an adversarial agent per
area that tries to break what the first one claims works.

## 5. Quality bar for "usable tomorrow"

- Every screen in `docs/design/renders/` exists, works and matches in both themes; every screen
  specified only in words (PRD §14A, §19.8, §20, and the PDF upload entry point) exists in the same
  style.
- No console errors, unhandled rejections or 5xx anywhere in tests, the simulation or production
  smoke runs.
- Kitchen first load on a throttled phone profile < 2.5 s on the local-vercel rehearsal (re-checked
  on production in the morning, < 4 s allowed with a cold Neon start); installable; offline read-only; push for every event type.
- Copy matches the PRD; no lorem ipsum, no TODOs in user-facing code.

## 6. P11 deploy-ready (verify locally tonight; Pranav deploys in the morning)

1. **Local Vercel rehearsal (`TARGET=local-vercel`):** run the exact production build of both
   services with `vercel build` + `vercel dev -L` (local mode, no login) or, if that can't run a
   service, the closest equivalent using the same entrypoints and `vercel.json`. Back it with local
   Postgres (Neon-compatible: pooled + direct URLs via PgBouncer in compose), the Blob fake, the
   queue fake, `EMAIL_PROVIDER=testinbox`, served over HTTPS (mkcert or Caddy) so install and push
   work. Enforce Vercel limits in this mode: 300 s max request duration, 4.5 MB body cap, 2 GB.
2. **Run against the rehearsal:** `make launch-check TARGET=local-vercel` (health; sign-up via
   email code; create house; second and third users join by link; text PDF bill → review → split →
   send; photo bill via browser OCR; payment; settle; kitchen item; ask → add to split; push to a
   test subscription; manifest, service worker and install criteria over HTTPS; security headers
   and CSP), `make e2e-multi TARGET=local-vercel`, a 10-minute
   `simulate_household.py --target local-vercel --minutes 10`, an SSE rollover at 270 s with no
   missed or duplicate events, and a 19 MB 10-page PDF uploaded via the client-upload path.
3. **Morning scripts, written and dry-run tested tonight** (use `--dry-run` that prints every
   command; test with a stubbed `vercel` CLI on PATH that records calls and returns realistic
   output, including failure cases):
   - `make deploy-setup`: checks `vercel whoami` (tells Pranav to run `vercel login` if needed),
     `vercel link --project kitchentab`, `vercel integration add neon` (free plan), creates the
     Blob store, enables Queues, generates and sets every env var for production and preview
     (VAPID keys, session/CSRF secrets, `TEST_INBOX_SECRET`, `EMAIL_PROVIDER=testinbox`, origins
     set after the first deploy). Idempotent: safe to re-run after a failure.
   - `make deploy`: lint + unit tests, Alembic migrations against Neon's direct URL,
     `vercel deploy --prod`, prints the URL, sets `WEB_ORIGIN`/`PUBLIC_LINK_BASE` and redeploys
     if they changed.
   - `make launch-check TARGET=prod` and `make e2e-multi TARGET=prod`: the same checks as step 2
     against the production URL using the test inbox; then `make prod-clean-test-data` (only
     rows tagged as test data).
   - `make set-email`: prompts for the Gmail address and app password, stores them with
     `vercel env add`, sets `EMAIL_PROVIDER=smtp`, removes `TEST_INBOX_SECRET`, disables
     `/api/test/*`, redeploys, sends a real code and asks Pranav to type it in. Also test it end to
     end locally against Mailpit.
   - `make redeploy` and `make rollback` for making changes on the go.
4. **`docs/LAUNCH.md`:** a 30–45 minute morning checklist in order: `npm i -g vercel`,
   `vercel login`, `make deploy-setup`, `make deploy`, `make launch-check TARGET=prod`, create the
   Gmail app password (link), `make set-email`, then sign in, create the house, share the invite
   link, install on iPhone (Safari → Share → Add to Home Screen, then allow notifications) and
   Android (Chrome → Install). Include what each failure message means and the fix, how to make
   and ship a change, back up, restore and roll back, and Hobby limits to watch.

## 7. Model, effort and tools

- You run on Opus at high effort. Use subagents for reviews (fresh context), wide searches and
  test-writing fan-outs; use a workflow for P10.
- Prefer the fakes and harnesses from TEST_PLAN §2 everywhere; real services only in §6.

## 8. Things only Pranav can do (never block on these)

Morning: `vercel login`, running the deploy scripts, the Gmail app password via `make set-email`,
testing on real phones. Optional later: Google OAuth client, Twilio Verify, `EXTRACTOR_API_KEY`.

## 9. Done

All true and shown in the transcript with command output:

1. All 10 milestones checked in `docs/PROGRESS.md`, with §1 changes applied to every milestone.
2. `make test-all` exited 0 on three consecutive runs (show each run's summary).
3. `simulate_household.py` passed 3 local seeds and 1 `local-vercel` run with zero invariant
   violations (show summaries).
4. `check_traceability.py` reports 100% (show output); `make test-extract` meets the C1 targets.
5. `docs/TEST_REPORT.md` shows 0 Must failures; every waiver has a reason. The only items left
   for the morning are the real Vercel deploy, the production checks, and real Gmail delivery.
6. `make launch-check TARGET=local-vercel` and `make e2e-multi TARGET=local-vercel` passed; every
   morning script passed its dry-run and stubbed-CLI tests (show output).
7. `docs/LAUNCH.md` and `docs/DEVICE_QA.md` exist and are complete.
8. Everything is committed and `git status` is clean. Nothing was deployed.

Then write a final message: what works, test numbers (incl. extraction accuracy), anything
waived, the exact morning steps from LAUNCH.md with time estimates, and the three areas a human
should check first on real phones.
