# KitchenTab build progress

Checkpoint for every session. Read `CLAUDE.md`, this file and `docs/BUILD_ALL.md` first, then
continue from the first unchecked item. A box is checked only when its tests pass, not when code
is written. Branch: `build/all`. No git remote is configured (nothing is pushed).

**Status line:** M1 + M2 built, reviewed (fresh-eyes review: 14 findings, backend ones fixed in
`a070e8a`); three web review items open (below). Backends for M3, M4 and M6 built and green (186 API
tests, 72 web unit tests, 40 Playwright, SYNC x200 local, SYNC x10 PROFILE=vercel). Screens for M3/M4
are being built by a subagent in apps/web. Next: close M1/M2 web review items, M3/M4 screens + gates,
M6 client (SSE, leader tab, polling fallback) and screen 5.

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
- [ ] Web review items: Undo toast pauses on hover/focus (WCAG 2.2.1); SplitCheckbox ignores key repeat;
      theme preference saved to the account too (PATCH /me)
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
- [ ] Server text-PDF extraction (pdfplumber/pypdf) + shared receipt parser with store profiles
- [ ] Browser OCR (tesseract.js self-hosted under /ocr/, pdfjs-dist), preprocessing
- [ ] 30 synthetic receipts with ground truth; `make test-extract` meets C1 targets
- [ ] Optional LLM plugin only when EXTRACTOR_API_KEY is set
- [ ] Gate

## Milestone 6: Real-time sync and open-bill life
IDs: SYNC-01..34, FR-32..FR-43, PAY-01..08, BILL-08..11, SPLIT-08, VERCEL-01, VERCEL-02, VERCEL-05 · Screen 5

- [x] API: SSE from Postgres `house_events` (750 ms poll, 270 s rollover), replay, gap fill, resync, revoked
- [x] Idempotency store and rate limits in Postgres; version checks; four write kinds; 426 STALE_CLIENT
- [x] API: member edits, change badges, activity, payments, reminders, settle, reopen, void (race tests at API level)
- [ ] Client: event cache patching, gap fill, leader tab (Web Locks + BroadcastChannel), polling fallback,
      tick coalescing; screen 5; multi-roommate browser tests for SYNC-01..24
- [ ] Gate (both profiles)

## Milestone 7: Tabs and balances
IDs: FR-50..FR-54, BAL-01..04, BILL-12, MONEY-07, MONEY-08, AUTH-12, AUTH-14 · Screens 1, Settings, House, Balances

- [x] API: balances (per-pair netting, carried), tabs lists with 20-per-page history, search, CSV export,
      sign-in methods, payment handles, data export, account deletion (anonymize, keep amounts)
- [ ] Screens: Tabs, Balances, House, Settings, house switcher
- [ ] Gate

## Milestone 8: Fridge and pantry
IDs: FR-60..FR-80, KITCH-01..16, ASK-01..14, MONEY-09 · Screens K1–K4

- [x] API: kitchen panels, fridge/pantry views with filter counts, sharing consent (OPEN only when every
      active owner says yes), use-soon/still-there in house tz, still good +3 d, used up + 60 s restore,
      leftovers, asks with limits, answers (free / split / chip in / decline), undo, cancel, 48 h expiry,
      member-left handling, history (24 tests in `tests/test_kitchen.py`, both profiles)
- [ ] KITCH-15 daily use-soon round-up (lands with the M9 outbox drain and cron)
- [ ] Screens K1–K4, item sheet, food icons; KITCH-16 / EDGE-19-10 virtualized long areas (Playwright)
- [ ] Gate

## Milestone 9: Install and notifications
IDs: PWA-01..12, OFFLINE-01..04, PUSH-01..18, EMAIL-01..05, VERCEL-04, VERCEL-09 · Screens N1–N2

- [ ] Vercel Queues adapter (fake locally) draining the outbox; delayed messages for quiet hours
- [ ] Gate (+ test-push)

## Milestone 10: Hardening and full verification
IDs: SEC-01..14, A11Y-01..08, VIS-01..04, COPY-01..02, PERF-01..06, CHAOS-01..08, DATA-01..06, E2E-01..10, VERCEL-06, VERCEL-07, VERCEL-08, VERCEL-10

- [ ] P10 full verification (BUILD_ALL §4), P11 deploy-ready (§6), P12 final (§9)

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

## Waivers

None yet.
