# KitchenTab build progress

Checkpoint for every session. Read `CLAUDE.md`, this file and `docs/BUILD_ALL.md` first, then
continue from the first unchecked item. A box is checked only when its tests pass, not when code
is written. Work on a branch and open a PR to https://github.com/Pranavimmaneni/kitchentab; `main` is
production (Vercel deploys every push), so merge only with the owner's go-ahead.

**Status (2026-10-11): LIVE at https://kitchentab.vercel.app, main = production, CI green.** Deployed
2026-10-11 with the owner's go-ahead: PR #3 (CI baseline + bug fixes), #4 (serverless connection pool,
ADR-0032), #5 (E2E-01..E2E-10 journeys, perf checks, CHECK? fix), #1 (custom split + item price; production
migrated to 0007 first with `make migrate-prod`), #2 (Settings), #7 (sheet-race fix N1 + E2E-05 round-up
sent "now" judged today, three CI races fixed). After each deploy: /api/v1/health/ready ok
(~0.12-0.2 s), no errors in the production logs. Simulations seeds 1-3 (30 min each): PASS. Release
traceability (`check_traceability.py --all`): OK (A11Y-08, EMAIL-04 waived to DEVICE_QA).

Previous status (2026-10-10, end of session 2): LIVE at https://kitchentab.vercel.app (Vercel team `ed-i-th`,
project `kitchentab`, Neon Postgres, private Blob store, sign-in codes from a dedicated KitchenTab Gmail via
SMTP). The household is using it. Code: https://github.com/Pranavimmaneni/kitchentab (private; the owner
invites collaborators). The Vercel project is connected to it: every push to `main` deploys production;
migrations are not run by Git deploys (`make migrate-prod`, see CONTRIBUTING.md). Vercel Hobby only deploys
private-repo commits authored by the owner: collaborators need a public repo, Vercel Pro, or owner merges. Everything is committed on `build/all` (working tree clean); see
docs/LAUNCH.md "Launch log" for what differed at launch (Vercel CLI 54 quirks, all fixed in scripts).

What works (verified): sign-up by email code, house + invite link, manual / PDF / photo (browser OCR)
bills, split grid, send + share link, payments/confirm/settle, tabs and balances, fridge/pantry, asks,
in-app notifications; local production-like run `make house` (http://localhost:3100, localhost only).
Gate before the last deploy: make lint green, web unit 229/229, API 538 passed (slow groups excluded).

NEW FEATURES REQUESTED BY THE USER (2026-10-10), do these FIRST, before the queue below:
A. Requests screen: LIVE 2026-10-11 (PR #1; production migrated to 0007 first).
   - FR-82: every request card (FOR YOU and YOU ASKED) shows the item's line total under its icon,
     "NO PRICE" for leftovers (ASK-18).
   - FR-81: answering with Add to split or Chip in offers Equal share (or Suggested) / 25% / 50% / 100% /
     Custom; Custom takes a whole percent (1-100) or the owner's own dollar value ($0.01 up to the price).
     The user's rule: "split either by percentage or add their own value". Open tab: the asker's
     allocation holds a fixed share of the line (share_num/share_den; p/100, or X/L for a value, so a
     value lands on the exact cent), PRD §6 + ADR-0030; owners split the rest by weight; 100% leaves the
     owners ticked at $0.00. Settled tab: chip-in = that part of the line. Server preview
     (GET /requests/{id}/answer-preview) shows the asker's part and every share delta before Say yes;
     Undo removes it. Tests: MONEY-11 (fixtures + Python==reference property + TS), ASK-15..ASK-18
     (API, unit, Playwright on WebKit and Chromium).
B. Settings: LIVE 2026-10-11 (PR #2; no migration).
   - FR-83: Save name used to change only the account; roommates kept seeing the old name. Now PATCH /me
     renames your member in every house (policy member.rename_self, one house write + member_renamed
     event per house, cut to 24 characters); a house where the name is taken keeps the old one and
     Settings says so (SET-01).
   - FR-84: order is name, NOTIFICATIONS (N2 link + email switch), Add KitchenTab to your home screen
     (iPhone/Android/computer steps, this device's first, Install button when offered, hidden in the
     home screen app), appearance, kitchen default, sign-in methods, payment handles, YOUR HOUSE
     (House settings link + Leave house), data and account (SET-02, SET-03).
Both shipped. Still ask the user before every production change.
C. Kitchen gestures (docs/features/kitchen-gestures/SPEC.md, FR-G01..G11): in progress on branch
   `feat/kitchen-gestures` (not deployed). Needs migration 0008 (`member_item_order`, item move stamps):
   the owner runs `make migrate-prod` from that branch before it merges. Its PR adds Milestone 11.

Open, in order (overnight queue):
1. GitHub CI (first runs 2026-10-10) was red on main. Fixed on branch fix/ci-baseline: ruff EXE001 (four
   scripts not executable in git), the test job had no MinIO (receipt tests, VERCEL-09 backup), A11Y-01
   target-size flagged a review row only where it sat under the sticky action bar (the audit now puts
   sticky bars in flow), EMAIL-05 depended on the hour (quiet hours hold email overnight; the test turns
   them off), E2E-01 on WebKit: the N1 sheet covered the Share button (product fix, assumption 55).
   CI e2e on main otherwise: 254 passed, 3 failed (those), 2 flaky (KITCH-13 WebKit, SYNC-13), 83 skipped.
   SYNC-24: decided (item 2); the Vercel-profile sync job needs the serverless pool PR.
   Old note: full local browser suite was 245 passed / 27 failed / 83 skipped, partly machine load.
   Possibly real: SYNC-32 tick flicker (WebKit), SYNC-31 leader hand-over > 3 s, SYNC-12 (WebKit/Firefox),
   SYNC-07 flaky, KITCH-07/08, ASK-06, BILL-03, E2E-01; VIS-02 and A11Y-07 need one confirmed run.
2. SYNC-24 p95 < 300 ms with 20 clients ticking flat out is not reachable with ticks serialized on the
   house lock (~15 ms each locally, slower on CI's 2-CPU runners): by Little's law the mean wait alone is
   about 20 x 15 ms = 300 ms. CI measured p95 1.0 s (local profile) and 4.0 s (vercel profile); this
   laptop 0.63 s. Owner decision pending: change the load model to the real client's (ticks batched
   every 250 ms per person) and keep the flat-out run for correctness + a throughput floor, or invest in
   a different lock design. Not changed without the owner (PRD §21, CLAUDE.md).
   DECIDED 2026-10-10 (owner: "Realistic load", ADR-0031): flat-out run keeps correctness + >= 20
   ticks/s; human-pace run (tap every 0.5-1.5 s, 250 ms coalescing) asserts p95 < 300 ms. Local profile
   passes both here. Vercel profile failed both (7 ticks/s, p95 4 s): every request opened a new DB
   connection and paid per-connection type lookups, some inside the house lock. Fix in a stacked PR
   (connection reuse in the serverless profile, ADR-0032).
   Branch perf/serverless-pool (stacked on fix/ci-baseline): DB_SERVERLESS_POOL_SIZE (default 10, 0 = old
   behaviour), VERCEL-11. Needs a real-deployment check before merging (ADR-0032).
   Earlier note: API timing tests on a busy machine: SYNC-24 p95 0.6 s vs 300 ms (design limit? ADR-0023 amendment),
   VERCEL-02, SYNC-25, write-helper load test. Re-measure on a quiet machine before deciding.
3. Verification (2026-10-11, branch test/e2e-journeys, stacked on perf/serverless-pool):
   - Journeys written and green on WebKit, Chromium and Firefox: E2E-01 (full, scanned Trader Joe's
     photo, flagged line fixed), E2E-02 (phone-code claim), E2E-04 (ask, push to the owner's device
     through the fake push service, Add to split), E2E-05 (use soon, round-up push, Used up, Undo),
     E2E-06 (settle with money owed, carry, reopen, settle again), E2E-10 (two payer devices, merge
     prompt). E2E-07 (install + push for every roommate action) on Chromium. E2E-08 is the screen-5
     offline test. Found and fixed on the way: a fixed CHECK? line kept its stamp (assumption 56).
   - PERF-04 green (link page JS 148.9 KB gz of 150: almost no headroom). PERF-06 green (200 items:
     fridge 445 ms, Kitchen 71 ms after data, CPU 4x).
   - NOT MET, owner's call: PERF-03 link page LCP 2.9 s (< 2.5), TBT 249 ms (< 200), CLS 0; PWA-12
     /start performance 0.89 (>= 0.90). The page is server-rendered; the late LCP looks like the web
     fonts repainting the heading over slow 4G (fix = font-display optional / fewer weights: a visible
     design trade-off). Lighthouse tests now run on Windows (path-with-space bug fixed).
   - NOT MET on this laptop (generator, DB and 4 API workers on one machine): PERF-01 p95 1.26 s at
     114 req/s (< 300 ms), PERF-02 delivery p95 10.6 s (< 2 s); 0 errors, 0 missed deliveries,
     invariants clean. Re-measure on Linux (a workflow_dispatch CI job would do).
   - PERF-05 green: 10 receipts read on the device in 2.5-3.8 s, p90 3.6 s (< 15 s).
   - `make simulate` seeds 1, 2, 3 (30 min each): all PASS, 32 checkpoints each, 0 errors, 0 violations,
     5 restarts each, 75 / 92 / 87 stream reconnects.
   - Left: A11Y-08 and EMAIL-04 are manual (waived to DEVICE_QA.md §7 and §9); `make test-all` three times; docs/TEST_REPORT.md; DEVICE_QA.md;
     milestone reviews and [done] marks.
4. EMAIL_POSTAL_ADDRESS (email footer) still empty: `make deploy-setup ARGS='--postal-address "..."'`
   then `make redeploy`. Push and install need checking on real phones (DEVICE_QA.md).
5. `make deploy` still runs its own lint+test gate; the last deploy ran the gate by hand
   (scratchpad helper). Run deploy scripts from a normal Windows terminal with
   KT_VERCEL_ENVIRONMENTS=production (the Claude desktop app sandboxes AppData: it can't see the
   user's global `vercel` or its login).

Never: deploy or change production settings without the user's go-ahead; handle passwords/app passwords.

### Fresh-eyes review of M8/M9/M10 backend: all 23 findings fixed
Highs: undo-split locking (#1), foreign owner ids (#2), house time zone + cron isolation (#3), the
tick/sharing deadlock (#4, removed at the source, ADR-0023 amendment). Mediums/lows: wake keys, savepoint
per notification, byte-based payload trim, 240 s drain budget, scheduled in-app round-ups re-checked,
snooze key, badge = live pending asks, expiry sweep via house_write events (GETs never write), advisory
lock in coalescing, push endpoint allowlist + no device takeover, worker digest, Neon URLs in tools,
422 for bad cursors, payer gets the tab notice. DATA-06 is now 1:1 by construction (every event gets an
activity row; house-level when it isn't part of a sent bill's story). Weak tests strengthened (PUSH-14
with a real rollback, SYNC-13 one event, PUSH-18 emoji, PUSH-06 decline/reject/reopen/void, KITCH-15 in
Europe/Berlin). Security suite findings fixed: NUL in any text (422 via NulGuardMiddleware), CSV formula
injection (apostrophe prefix), cross-house member/guest ids on exclusions and payments (404).
Still failing: SYNC-24 p95 (0.6 s at ~67 ticks/s/house locally; see ADR-0023 amendment).

`scripts/check_traceability.py` reads the `Milestone N` headings below: a milestone whose heading
ends with `[done]` must have every FR and TEST_PLAN ID listed under its "IDs" line covered by a
test.

---

## Phase P0: Reconcile (BUILD_ALL §2)

- [x] Branch `build/all` created; BUILD_ALL.md committed
- [x] Verified real state: M1 backend parts pass (`uv run pytest`: 66 passed), ruff and mypy clean
- [x] Fixed 7 failing settings tests (env leak from conftest; pydantic wraps validator errors)
- [x] Apply §1 (C1 local extraction, C2 Vercel, C3 day-one sign-in) to PRD §3, §5.2, §9, §11, §12, §13, §18
- [x] Apply §1 to TEST_PLAN (EXTRACT-01..16, AUTH-15/16, VERCEL-01..10, new make targets)
- [x] ADRs 0001-0017 (`docs/decisions/`)
- [x] Retrofit §1 into built code: settings (PROFILE, email smtp/testinbox, blob, queue), Postgres
      rate limits / idempotency / test inbox (migration 0002), Redis optional in `PROFILE=vercel`
- [ ] Finish the rest of M1 (web app, Makefile, CI, traceability script, edge-case IDs)
- [ ] Gate: `make lint test` green

## Milestone 1: Foundation

IDs: DATA-01, SYNC-25, SYNC-26, SEC-11, PWA-10, A11Y-05

- [x] Monorepo layout; uv project `apps/api` (Python 3.12)
- [x] docker-compose: Postgres 16, Redis, MinIO (bitnamilegacy image, ADR-0008), Mailpit, mock OIDC, fake push
- [x] FastAPI skeleton: health/readiness, settings validated at boot, RFC 9457 errors, JSON logs with redaction
- [x] Alembic baseline 0001 with every table/constraint in PRD §10, §19.7, §20.2, §21.8 (DATA-01)
- [x] `house_write` helper: house lock last, gapless seq, events/activity/outbox in one commit (SYNC-25, SYNC-26)
- [x] Fakes: SMS, email→Mailpit, extractor, mock Google OIDC, fake Web Push (VAPID + RFC 8291)
- [x] Harnesses: scenario seeder + `tests/scenarios/apt4b.yaml`, race helper, event recorder, clock control, test routes (SEC-11)
- [x] Next.js app: tokens.css (both themes + kitchen tokens), light/dark/system theming, manifest stub, fonts
- [x] §14B base components + `/dev/components` (both themes; WebKit, Chromium, Firefox)
- [x] Multi-roommate Playwright fixture `roommates(n)` + `expectEverywhere`
- [x] Fresh-eyes review; backend findings fixed
- [x] Web review items: Undo toast pauses on hover/focus (WCAG 2.2.1); SplitCheckbox ignores key repeat;
      theme preference saved to the account too (PATCH /me) and applied from /me on load
- [x] Makefile (every CLAUDE.md target, `PROFILE=vercel`, `TARGET=`), GitHub Actions CI
- [x] `scripts/check_traceability.py` + `docs/traceability/edge_cases.yaml` (self-tested)
- [ ] Gate (lint, test, review)

## Milestone 2: Money core
IDs: MONEY-01, MONEY-02, MONEY-03, MONEY-04, MONEY-05, MONEY-06, MONEY-10, FR-23

- [x] `app/domain/money.py` passes `money_cases.json` (incl. adjustment cases); 100% branch coverage
- [x] Python property tests: 10,000-bill corpus digest == reference; hypothesis invariant; tie-breaks; no-float scan
- [x] `apps/web/lib/money/` (TS mirror) + corpus digest + fast-check; TS no-float lint (17 tests)
- [ ] Gate

## Milestone 3: Auth and houses
IDs: FR-01..FR-09, AUTH-01..16, HOUSE-01..10, LINK-01..06 · Screens A–D

- [x] Email codes (C3: smtp / testinbox / Mailpit), phone + Google behind fakes, buttons hidden without keys
- [x] Sessions, CSRF, houses, placeholders, invite links (derived tokens, ADR-0018), join + claim (API, 32 tests)
- [ ] Screens A–D
- [ ] Gate

## Milestone 4: Bills without OCR
IDs: FR-10, FR-13..FR-17, FR-20..FR-27, FR-30, FR-31, FR-60 (tag), BILL-01..07, SPLIT-01..07, SPLIT-09, SPLIT-10, LINK-05 · Screens 2–4, E

- [x] API: manual entry, item CRUD/merge/split/reorder, storage tags, allocations, fill-rest, exclusions, guests, send, bill link + claim (18 tests)
- [x] Reconciliation override with adjustment line (ADR-0002)
- [ ] Screens 2-4, E, link page + OG image, share card
- [ ] Gate

## Milestone 5: Receipt extraction (C1: local, no API key)
IDs: FR-10..FR-12, FR-18, FR-60, EXTRACT-01..16, VERCEL-03

- [ ] Blob/MinIO client uploads (20 MB, 10 pages), PDF first-class input, drag-and-drop
- [x] Web (ADR-0026): capture sheet (camera, photo library, "Upload PDF", desktop drag-and-drop, by
      hand), on-device refusals (magic bytes, 20 MB, 10 pages), EXIF stripped by canvas re-encode,
      client upload (MinIO PUT / `@vercel/blob/client`), reading + failure states with Try again /
      Enter by hand, "Use the scanned items instead?" banner, receipt photo; `/api/blob-upload` and
      `/api/blob-view` routes (HMAC pinned to `storage.sign_read`); `tests/e2e/receipts.spec.ts`
      (EXTRACT-01/02/06/12/14, SYNC-15) green on WebKit, Chromium and Firefox; unit tests for files,
      EXIF, preprocessing, scan job and both routes
- [ ] Server text-PDF extraction (pdfplumber/pypdf) + shared receipt parser with store profiles
- [x] Browser OCR (tesseract.js 5 self-hosted under /ocr/, pdfjs-dist 4, eng best_int committed),
      preprocessing copied from scripts/receipts/preprocess.js: 5 photos + 1 scanned PDF read in
      WebKit and Chromium within the C1 targets, no request off the app's hosts (EXTRACT-12)
- [ ] 30 synthetic receipts with ground truth; `make test-extract` meets C1 targets
- [ ] Optional LLM plugin only when EXTRACTOR_API_KEY is set
- [ ] Gate

## Milestone 6: Real-time sync and open-bill life
IDs: SYNC-01..35, FR-32..FR-43, PAY-01..08, BILL-08..11, SPLIT-08, VERCEL-01, VERCEL-02, VERCEL-05 · Screen 5

- [x] API: SSE from Postgres `house_events` (750 ms poll, 270 s rollover), replay, gap fill, resync, revoked
- [x] Idempotency store and rate limits in Postgres; version checks; four write kinds; 426 STALE_CLIENT
- [x] API: member edits, change badges, activity, payments, reminders, settle, reopen, void (race tests at API level)
- [x] Client (`lib/sync/*`): event reducer + cache patching, in-order sequencer with `?after=` gap fill,
      resync/revoked/rollover, leader tab (Web Locks + BroadcastChannel relay), backoff + polling fallback,
      echo suppression by mutation id, offline banner and disabled writes; tick coalescing (M4)
- [x] Screen 5 (`/bills/[id]`): who owes, payments (pay sheet, confirm/reject, cash), remind, settle,
      reopen, void, story with cursor, change banner + PUT seen, split grid (own column / payer), item
      edit with conflict prompt; API adds `payer_handles` to `GET /bills/{id}/money` (PAY-04/05)
- [x] Multi-roommate browser tests (`tests/e2e/sync.spec.ts`, 12 × 3 browsers): SYNC-01/03/05/06/12/
      31/32/34, PAY-01/07, BILL-08/09/10, SPLIT-08, OFFLINE-01/02/04
- [ ] Not yet: SYNC-22 (deploy toast / 426 prompt, with PWA-09 in M9), PAY-08 (auto-reminders, M9 outbox)
- [x] Web stabilization pass (2026-10-09): more browser races (`sync.spec.ts` SYNC-02/04/09/10/23,
      `kitchen-sync.spec.ts` SYNC-07/13), AUTH-09 sign out everywhere (`session.spec.ts`; the stream
      re-reads its session every second and sends `revoked {reason: "signed_out"}`, API test in
      `test_sync_stream.py`), network rules (`network.spec.ts`: EDGE-15-25 long-open banner, EDGE-15-26,
      OFFLINE-03/04, CHAOS-06 "Saving…", CHAOS-08 server time from the `Date` header), screen audits
      (`screens-quality.spec.ts`: A11Y-01/04/06/07, VIS-02 on 26 screens, Chromium), SPLIT-10
      (`split-wide.spec.ts`). Writes are refused, never held, while offline (`lib/api/client.ts`).
- [ ] Gate (both profiles)

## Milestone 7: Tabs and balances
IDs: FR-50..FR-54, FR-83, FR-84, BAL-01..04, BILL-12, MONEY-07, MONEY-08, AUTH-12, AUTH-14, SET-01..03 · Screens 1, Settings, House, Balances

- [x] API: balances (per-pair netting, carried), tabs lists with 20-per-page history, search, CSV export,
      sign-in methods, payment handles, data export, account deletion (anonymize, keep amounts)
- [x] Screens: Tabs (`/tabs`), Balances, House, Settings, house switcher (web unit tests in
      `tests/unit/tabs.test.ts`; Playwright `tests/e2e/tabs-house.spec.ts` on WebKit + Chromium; screenshots
      and axe in `tests/e2e/kitchen-screenshots.spec.ts`)
- [x] API addition: tab cards carry `changed.by_name` / `changed.at` for "{name} tweaked it {day}"
- [x] BAL-02 (balances converge on every device within 2 s): `sync.spec.ts` "house screens live"
- [ ] Gate

## Milestone 8: Fridge and pantry
IDs: FR-60..FR-82, KITCH-01..16, ASK-01..18, MONEY-09, MONEY-11 · Screens K1–K4

- [x] API: kitchen panels, fridge/pantry views with filter counts, sharing consent (OPEN only when every
      active owner says yes), use-soon/still-there in house tz, still good +3 d, used up + 60 s restore,
      leftovers, asks with limits, answers (free / split / chip in / decline), undo, cancel, 48 h expiry,
      member-left handling, history (24 tests in `tests/test_kitchen.py`, both profiles)
- [ ] KITCH-15 daily use-soon round-up (lands with the M9 outbox drain and cron)
- [x] Screens K1–K4, item sheet, add leftovers, 51 food icons; KITCH-16 / EDGE-19-10 windowed rows past
      40 tiles (page scroll, no inner panel) — `tests/e2e/kitchen.spec.ts`, `tests/unit/kitchen*.test.ts`
- [x] API additions: request cards carry `owners`, `source`, `line_total_cents`; item views carry `granted_by`
- [ ] Gate

## Milestone 9: Install and notifications
IDs: PWA-01..12, OFFLINE-01..04, PUSH-01..19, EMAIL-01..05, VERCEL-04, VERCEL-09 · Screens N1–N2

- [x] API: outbox drain (arq locally, Vercel Queues `outbox-drain` subscriber, inline in tests), fan-out
      for every §20.2 event type, never the actor, dedupe keys, inbox rows, preferences matrix (N2
      defaults), quiet hours with grouped morning delivery, 10-minute share coalescing, 50¢ threshold,
      daily cap 20, retries, private lock screen, subscriptions (404/410 delete, 5 failures stale),
      `POST /push/test`, badge, inbox + read, email with List-Unsubscribe / one-click unsubscribe /
      footer / dark-mode HTML, weekly digest, daily cron + sweeper (ADR-0021; `tests/test_notify.py`,
      23 tests, both profiles; VERCEL-04 runs the real Queues SDK against its embedded service)
- [ ] Daily logical export to storage (VERCEL-09 "export" part; lands with `make restore-drill`)
- [x] Web (ADR-0027): manifest with screenshots + monochrome icon, Serwist worker (precache, offline
      page, network-first 3 s reads marked stale, never-cache list), update toast + 426 prompt,
      install card / iPhone N1 steps / Settings install, `pwa_installed`, AUTH-13 welcome, offline
      banner side, N1 + N2 (`/settings/notifications`), subscribe + re-register on start +
      `pushsubscriptionchange`, push / `notificationclick` handlers, app badge — `tests/e2e/pwa.spec.ts`,
      `push.spec.ts`, `offline.spec.ts`, `email.spec.ts` (PWA-01..11, OFFLINE-01/02, PUSH-01/02/03/04/09/
      15/16/17, AUTH-13, SYNC-22, EMAIL-05) + unit tests for the routing rules, strategy, handlers, moments
- [ ] PWA-12: Lighthouse 11.7.1 mobile on `/start` (production build): accessibility 100, best
      practices 100, PWA audit passes, performance 84–89 with other suites saturating the CPU (TBT
      400–490 ms, all in the shared React/Next chunks). `tests/e2e/lighthouse.spec.ts` runs it with
      KT_LIGHTHOUSE=1; rerun on a quiet machine
- [x] `make test-push` web part: `tests/e2e/push.spec.ts` (grep "PUSH-"); EMAIL-04 (DNS) is a launch-check item; EMAIL-05 screenshots
- [ ] Gate (+ test-push)

## Milestone 10: Hardening and full verification
IDs: SEC-01..14, A11Y-01..08, VIS-01..04, COPY-01..02, PERF-01..06, CHAOS-01..08, DATA-01..06, E2E-01..10, VERCEL-06, VERCEL-07, VERCEL-08, VERCEL-10, VERCEL-11

- [x] Rate limits: 60 writes/min per user (across devices), 300 reads/min per IP, 429 + Retry-After
      (SEC-05); production refuses to boot with them off; the shared test app turns them off for SYNC x200
- [x] Policy: every authorization check in `app/policy.py` (moved the payer-only open-bill checks, kitchen
      owner/asker checks and device ownership in); `tests/test_policy.py` covers every cell, 100% branches
- [x] Backups: `app/services/backup.py` logical export (tagged JSON lines, gzip, FK order) + restore into a
      fresh database; `make restore-drill` (DATA-02) and `scripts/check_invariants.py` / `make check-invariants`
      (DATA-05: money, kitchen owners, balances net to zero, gapless events, notifications)
- [ ] Daily export written to storage by the cron (needs the storage adapter from M5 integration)
- [ ] SEC-01 HTTP matrix, SEC-02..04, SEC-06..09, SEC-11, SEC-12, SEC-14; CSP/HSTS headers (web + API)
- [ ] A11Y, VIS, COPY, PERF (k6), CHAOS (Toxiproxy), DATA-01/03/06, E2E-01..10
- [x] DATA-06 (one event per state change, activity 1:1), SYNC-24 at API level against 4 uvicorn worker
      processes (`tests/harness/multiserver.py`) with the invariant checker: found and fixed two tick races
      (ADR-0023); its p95 < 300 ms check still has to pass on a quiet machine (last run: other agents saturating CPU)
- [ ] Live household simulation `scripts/simulate_household.py` / `make simulate` (BUILD_ALL §4.3): 4
      people on 6 API devices with reconnecting streams; manual, PDF and photo (client OCR) bills, ticks,
      If-Match price edits, payments with double taps and lost responses, confirm/reject, settle/reopen,
      asks answered every way + undo, used up/restore, sharing, member removed + rejoined, tab sleeps,
      server restarts; checks every 30 s (invariants, gapless/duplicate-free streams, devices agree, no
      duplicate push, and any 5xx on reads or drains). Runs on 3 single-process API instances, streams on a
      different instance than writes. Found and fixed (ADR-0029, regression tests in
      `tests/test_races_found_by_simulation.py`): torn reads → 500 on `/tabs` (SYNC-35, GET views now read one
      REPEATABLE READ snapshot); drain deadlock on device rows → failed drain → pushes sent twice (PUSH-19,
      device bookkeeping in its own short transaction; MissingGreenlet in the error path); harness: orphaned
      `uvicorn --workers` children exhausted Postgres and Windows' shared socket hung new connections
      (instances + process-tree stop). Still to do: 3 browser devices (after the web journeys land),
      3 × 30-minute seeds, 1 local-vercel run
- [ ] `make test-load` (`scripts/load_test.py` + `load/perf01.js`): PERF-01 k6 200 people / 50 bills, PERF-02
      1,000 streams / 200 houses, then the invariant checker. Written; needs a quiet machine to measure
- [ ] P10 full verification (BUILD_ALL §4), P11 deploy-ready (§6), P12 final (§9)

## P11: Deploy-ready (BUILD_ALL §6)

- [x] `vercel.json` (services web + api, `/api/blob-upload` → web, `/api/(.*)` → api, daily cron),
      `app/vercel_entry.py`, queue subscriber declared in apps/api/pyproject.toml (VERCEL-06/07/08 static tests)
- [x] Settings accept Neon's libpq URLs (asyncpg driver, `ssl`, no `channel_binding`); preview deployments take
      their origin from `VERCEL_URL`
- [x] Morning scripts: `make deploy-setup deploy redeploy rollback set-email prod-clean-test-data` with
      `DRY_RUN=1` and 44 stubbed-CLI tests (VERCEL-10, ADR-0022). Production checks must sign up with
      `@launch-check.kitchentab.test` emails or `prod-clean-test-data` won't find their rows
- [x] `docs/LAUNCH.md` drafted (finalize after launch-check and the local-vercel rehearsal exist)
- [x] local-vercel rehearsal (`make local-vercel-up/-down`, ADR-0028): `next build`/`next start` + `app.vercel_entry:app`
      behind a Caddy edge with vercel.json routing on https://localhost:8443 (local CA), 4.5 MB → 413, 300 s write
      deadline; PgBouncer (transaction) + its own Postgres `kitchentab_lv`; fake Blob on its own HTTPS origin; queue
      devserver + the `outbox-drain` subscriber via `poll_and_handle`; fake push; APP_ENV=test with a deployed-mode
      (staging) settings preflight
- [x] `make launch-check TARGET=local-vercel|prod` (`scripts/launch_check.py`, 17 steps, 16 unit tests in
      scripts/tests/test_launch_check.py) + `@launch` Playwright spec (installability via CDP, service worker, browser
      OCR). Full run 2026-10-09: 16/17 PASS incl. 19.9 MB 10-page PDF via client upload, 5 MB JSON → 413 at the
      edge, SSE server rollover at 271 s with 20 events gapless and no duplicates, push through queue + subscriber;
      both `@launch` browser checks pass. `simulate_household --target local-vercel --minutes 10`: PASS, 0 violations
- [ ] launch-check fully green: the only FAIL is the web pages' security headers (no CSP, nosniff, Referrer-Policy,
      frame-ancestors on Next.js pages yet; M10 SEC-03). `make e2e-multi TARGET=local-vercel`: roommate-claim and
      BAL-02 pass; AUTH-09 fails on all 3 browsers (revoked arrives 1.0–2.2 s after "Sign out everywhere" through
      the 1 s session recheck + 750 ms poll + NullPool/PgBouncer connections; the 2 s budget is too tight in
      PROFILE=vercel) and BAL-02 is timing-flaky on one browser per run
- [ ] Daily export written to Blob by the cron (`backups/kitchentab-YYYY-MM-DD.jsonl.gz`)

---

## Assumptions

Decisions taken where the PRD was silent or contradictory. Each has an ADR in `docs/decisions/`.

1. Write kinds, idempotency and realtime follow PRD §21 over §11 (one house stream, no per-bill stream). ADR-0001.
2. Pairwise balances are netted per pair across bills; no multi-party simplification (FR-51). ADR-0001.
3. Bill-level amounts are rounded one at a time; the reference script defines the math. ADR-0001.
4. Milestone numbering follows PRD §17 (Install = 9, Hardening = 10).
5. Reminders: at most one per member per bill per 48 h (FR-41, PAY-07). Share-changed email keeps the 6 h digest; push coalesces over 10 min.
6. Fake providers allowed in local and test; `/__test/*` only with APP_ENV=test + TEST_ROUTES_ENABLED; boot fails otherwise (SEC-11).
7. Horizontal scroll is allowed inside the split grid past 5 members; no vertical inner scroll anywhere.
8. E2E also runs on Firefox desktop (TEST_PLAN over PRD §16); 16 mockup screens, not 15.
9. Guests rank after members (creation order) for rounding tie-breaks.
10. CLAUDE.md rule 3 applies to house-scoped writes; user-level changes visible in several houses emit one event per house.
11. Schema gap fills (users.timezone, notification delivery columns, refunds as payment direction, `void_credit` ledger source, anonymized members excluded from the name index, rejoin reactivates the member row, admin transfer required before admin leaves). ADR-0003.
12. FR-13 override: `printed_total_cents` + signed `adjustment` bill fee allocated like tax. ADR-0002.
13. Member-or-guest references are two nullable FKs with CHECK exactly one. ADR-0003.
14. Traceability is milestone-aware, driven by this file; §15/§19.9 rows get EDGE-15-nn / EDGE-19-nn IDs in `docs/traceability/edge_cases.yaml`. ADR-0004.
15. Contrast: empty controls use `--control-edge` (= muted); violet member color #6A4CFF. ADR-0005.
16. MinIO image from `bitnamilegacy/minio:2025.4.22` (Docker Hub `minio/minio` no longer exists). ADR-0008.
17. `joserfc` (already Authlib's dependency) used directly by the mock OIDC issuer; OAuth form bodies parsed with the stdlib instead of adding python-multipart. ADR-0009.
18. `share_snapshots.bill_version` added to compute "changed since you last looked" deltas against `seen_markers`.
19. apt4b scenario clock is 2026-10-03 19:00 EDT; kitchen use-soon states that the §19.5 rules can't produce from the purchase date use explicit `stocked_on` / `shelf_life_days` overrides.
20. Commit-order proof for SYNC-25 uses Postgres `track_commit_timestamp=on` (compose and Testcontainers).
21. Drafts: the uploader and the payer can both edit; once open only the payer (policy `_decide_bill`).
22. `bills.receipt_total_cents` holds the printed total that review reconciles against (migration 0003).
23. Link tokens are derived (ADR-0018) so members can see invite/bill links again.
24. `tzdata` added (zoneinfo on Windows dev machines); `joserfc` declared (Authlib's own dependency).
25. Fake SMS codes live in Postgres (`test_inbox`, subject `sms`) so no profile needs Redis for tests.
26. Row locks are `FOR NO KEY UPDATE` (ticks take `FOR SHARE` on the bill) so FK checks never deadlock.
27. The "changed" badge compares my share now with my share when I last looked (seen marker time), because ticks don't bump the bill version.
28. Settle with outstanding money writes carryover ledger entries; reopen writes reversing entries (nothing financial deleted).
29. Chunked request bodies are buffered up to 4.5 MB and refused beyond; X-Forwarded-For is trusted only with PROFILE=vercel.
30. `MIN_CLIENT_VERSION` (default 0) drives 426 STALE_CLIENT from `X-Client-Version`.
31. Notification details the PRD left open (dedupe key shape, inbox rows for every type, coalescing from commit time, grouped quiet-hours delivery, badge = unread asks + share rows, dev VAPID keys derived from SESSION_SECRET, email only to verified addresses, signed unsubscribe with a confirm page): ADR-0021.
32. `EMAIL_POSTAL_ADDRESS` (new, optional) is printed in notification email footers; it must be set before launch (LAUNCH.md).
33. Routes follow PRD §19.8 / §14A: the Kitchen (K1) is `/` and Tabs (screen 1) is `/tabs`.
34. The current house is the one last picked in the switcher on this device (localStorage `kt.house`), else the most recently joined (FR-08).
35. Fridge/pantry filters are applied on the client from the server's item flags (`mine`, `open_for_me`, `use_soon`) so switching never refetches or moves tiles; counts are the server's. OPEN means open for me, as the API counts it.
36. Areas with more than 40 tiles render only the rows near the viewport (fixed 84 px rows, page scroll), which is the KITCH-16 "virtualizes" behavior.
37. Food-icon and illustration fills are tokens (`--ill-*` in tokens.css), the same in both themes (sticker look).
38. Settings has one email toggle (`notify_email`) until N2 (`/settings/notifications`) lands in M9; adding Google as a sign-in method from Settings is not offered (the API attaches email and phone only).
39. Balances "Remind" sends one reminder per open tab behind the pair (`POST /bills/{id}/remind`); "Pay" opens the tab I owe most on (the pay sheet lives on screen 5).
40. Stream resume uses `?last_event_id=N` on a fresh EventSource (it cannot set `Last-Event-ID`); the client closes and reopens itself on errors so backoff and the polling fallback are its own.
41. A fresh stream (no baseline) refetches what the house shows once on `ready`, covering commits between the first GETs and the stream opening; polling with no baseline takes it from `?after=0`.
42. Own echoes: patches skipped and the optimistic bill not refetched; money, activity and lists still refresh. A patch or tick result that lands while a GET is in flight cancels that GET and refetches after (no stale overwrite).
43. A removed member (stream event, `revoked`, or a write answered NOT_A_MEMBER) is sent to `/` with "You're not in {house} any more." (there is no join page without a token).
44. The change badge: the first `/money` load shows "Your share changed" and records PUT seen at once; changes made while viewing show live; Dismiss PUTs seen again.
45. Payment reminders: the bell hides for 48 h after a nudge (from the loaded story and this session); 429 shows the server's copy.
46. Payer handles appear on `GET /bills/{id}/money` as `payer_handles` (minimal API addition for the pay sheet, PRD §8/§14A).
47. `next.config.ts` honors `KT_DIST_DIR` so two local Playwright stacks can build side by side.
48. The local-vercel rehearsal runs the API with APP_ENV=test (no test routes) because deployed-mode validation needs Vercel Blob, a non-localhost origin and real push services; a staging preflight validates the same environment, and it has its own Postgres so it never competes with the test suites. ADR-0028.
49. A scan started on this device lives in memory until it settles; after a reload a draft left `pending`/`failed` with no items shows the failure state with "Choose the file again" (the stored file stays attached). ADR-0026.
50. The install card and N1 sheet render from the root layout (`components/pwa/PwaRoot.tsx`); "Settings › Install app" lives on `/settings/notifications`, which Settings links to. Prompt memory is per device (localStorage). ADR-0027.
51. `pwa_installed` and install events go to `window.__ktEvents` until an analytics provider is chosen. ADR-0027.
52. A "visit" is a new browser session (tab or app launch); the 2nd visit is an install moment. The "first ask" moment is an ask sent from this app or any ask/answer in the inbox.
53. Playwright blocks service workers by default; PWA/push/offline specs opt in, and push specs use the full Chromium build (`channel: "chromium"`) because the headless shell always reports notification permission as denied. ADR-0027.
54. `X-Client-Version` is the integer API compatibility level (`1`), compared numerically with MIN_CLIENT_VERSION. ADR-0026.
55. The first-bill notifications pre-prompt (N1) waits until the payer leaves E · Share: a write moment is decided on the next idle check and Share is marked busy (`data-kt-busy`), so the sheet never lands on the Share button (PUSH-01, E2E-01; PRD §20.2 rule 1 clarified).
56. A CHECK? (flagged) line is cleared once the payer edits its name, quantity or price: they checked it. Tapping its storage tag leaves the stamp (EXTRACT-05, FR-12).

## Waivers

- A11Y-08: manual by definition (TEST_PLAN: a VoiceOver/TalkBack pass on real phones). The owner
  runs DEVICE_QA.md §7 before a release and records it under "Device QA" in TEST_REPORT.md.
- EMAIL-04: manual by definition (TEST_PLAN: a DNS check at release). The owner runs DEVICE_QA.md §9
  (Show original: SPF, DKIM, DMARC PASS) and records it under "Device QA" in TEST_REPORT.md.
- GHSA-vfj7-8cjw-p6xm (CVE-2026-93687; `braces@3.0.3`, high; dependency audit, ADR-0025): no patched
  release exists (the advisory lists no fixed version). Reached only through lint tooling
  (`eslint-config-next > @next/eslint-plugin-next > fast-glob > micromatch > braces`), which expands
  our own repository's glob patterns during `pnpm lint`; nothing of it is in the built bundle or the
  API. Revisit when eslint-config-next moves off fast-glob 3.3.1 / micromatch 4.
