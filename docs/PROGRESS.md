# KitchenTab build progress

Checkpoint for every session. Read `CLAUDE.md`, this file and `docs/BUILD_ALL.md` first, then
continue from the first unchecked item. A box is checked only when its tests pass, not when code
is written. Branch: `build/all`. No git remote is configured (nothing is pushed).

**Status line:** Phase P0 (Reconcile) in progress. Last green: backend `make test` (66 tests),
ruff + mypy clean, commit `9d7f1d2`.

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
- [ ] Retrofit §1 into built code: settings (PROFILE, email smtp/testinbox, blob, queue), Postgres
      realtime/rate limits/idempotency/test inbox tables (migration 0002), Redis optional in
      `PROFILE=vercel`
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
- [ ] Next.js app: tokens.css (both themes + kitchen tokens), light/dark/system theming, manifest stub, fonts
- [ ] §14B base components + `/dev/components` (both themes)
- [ ] Multi-roommate Playwright fixture `roommates(n)` + `expectEverywhere`
- [ ] Makefile (every CLAUDE.md target, `PROFILE=vercel`, `TARGET=`), GitHub Actions CI
- [ ] `scripts/check_traceability.py` + `docs/traceability/edge_cases.yaml`
- [ ] Gate (lint, test, review)

## Milestone 2: Money core
IDs: MONEY-01, MONEY-02, MONEY-03, MONEY-04, MONEY-05, MONEY-06, MONEY-10, FR-23

- [ ] `app/domain/money.py` and `apps/web/lib/money/` pass `money_cases.json` (incl. adjustment cases)
- [ ] Property test 10,000 bills; Python == TS; tie-breaks; no-float lint rule
- [ ] Gate

## Milestone 3: Auth and houses
IDs: FR-01..FR-09, AUTH-01..16, HOUSE-01..10, LINK-01..06 · Screens A–D

- [ ] Email codes (C3: smtp / testinbox / Mailpit), phone + Google behind fakes, buttons hidden without keys
- [ ] Sessions, CSRF, houses, placeholders, invite links, join + claim
- [ ] Screens A–D
- [ ] Gate

## Milestone 4: Bills without OCR
IDs: FR-10, FR-13..FR-17, FR-20..FR-27, FR-30, FR-31, FR-60 (tag), BILL-01..07, SPLIT-01..07, SPLIT-09, SPLIT-10, LINK-05 · Screens 2–4, E

- [ ] Manual entry, item CRUD, storage tags, split grid, review, send, link page + OG, share card
- [ ] Reconciliation override with adjustment line (ADR-0002)
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

- [ ] SSE from Postgres `house_events` (750 ms poll, 270 s rollover), replay, gap fill, leader tab
- [ ] Idempotency store and rate limits in Postgres; version checks; four write kinds
- [ ] Member edits, change badges, activity, payments, reminders, settle, reopen, void
- [ ] Gate (both profiles)

## Milestone 7: Tabs and balances
IDs: FR-50..FR-54, BAL-01..04, BILL-12, MONEY-07, MONEY-08, AUTH-12, AUTH-14 · Screens 1, Settings, House, Balances

- [ ] Gate

## Milestone 8: Fridge and pantry
IDs: FR-60..FR-80, KITCH-01..16, ASK-01..14, MONEY-09 · Screens K1–K4

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

## Waivers

None yet.
