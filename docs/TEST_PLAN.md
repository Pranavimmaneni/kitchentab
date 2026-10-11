# KitchenTab — Test Plan

This is the complete verification plan for KitchenTab. Claude Code builds the tests alongside each
milestone (PRD §17) and runs the whole plan at the end (prompt 4 in `KICKOFF_PROMPT.md`). Nothing
ships until every **Must** test passes, the traceability check is green, and `docs/TEST_REPORT.md`
is filled in.

- **Test IDs** are stable. Name tests with their ID, e.g. `test_SYNC_03_last_two_untick`, or tag them
  (`@pytest.mark.id("SYNC-03")`, Playwright `test('SYNC-03 …')`).
- **FR traceability:** every FR-xx in the PRD must be covered by at least one test that carries
  `fr("FR-xx")`. `scripts/check_traceability.py` fails CI if any FR has no test.
- **Priority:** M = Must (blocks release), S = Should (fix or waive in the report with a reason).

---

## 1. How to run

| Command | What it runs | When |
| --- | --- | --- |
| `make test` | Unit + integration (backend and frontend) | Every commit |
| `make test-sync` | Concurrency and race suite (SYNC-*), 200 iterations each | Every PR touching writes |
| `make e2e` | Playwright journeys on WebKit (iPhone 14), Chromium (Pixel 7), Firefox desktop | Every PR |
| `make e2e-multi` | Multi-roommate journeys (3–4 browser contexts at once) | Every PR |
| `make test-push` | Push pipeline against the fake push service + service-worker tests | Every PR touching notifications |
| `make test-chaos` | Network and dependency failures via Toxiproxy | Nightly + before release |
| `make test-load` | k6 load scenarios | Before release |
| `make test-security` | Authorization matrix + ZAP baseline + dependency audit | Nightly + before release |
| `make test-a11y` / `make test-visual` | axe on every screen, both themes / screenshot diffs vs design | Every PR |
| `make test-extract` | Extraction accuracy on the 30 generated receipts (C1 targets) + browser OCR on WebKit and Chromium | Every PR touching extraction |
| `make golden` | Alias of `make test-extract` (kept for the original plan) | Nightly |
| `make test-sync PROFILE=vercel`, `make e2e-multi PROFILE=vercel` | The same suites with production adapters (Postgres realtime, queue SDK, fake Blob, test inbox) | Every PR touching writes |
| `make launch-check TARGET=local-vercel` | Production build rehearsal end to end (BUILD_ALL §6) | Before release |
| `make test-all` | Everything above, then writes `docs/TEST_REPORT.md` | Release candidate |

---

## 2. Harnesses and fakes (build these first)

| Harness | Requirement |
| --- | --- |
| **Scenario seeder** | `make seed SCENARIO=apt4b` builds the mockup world: house Apt 4B, members Pranav (admin), Arjun, Meera, Kabir, the ShopRite and H Mart bills, kitchen items and asks exactly as in `docs/design/`. Scenarios live in `tests/scenarios/*.yaml` and are reused by unit, e2e and manual QA. |
| **Fake providers** | SMS (records codes, `GET /__test/sms`), email (Mailpit), Google OIDC (local mock issuer that signs tokens for any test email), receipt extractor (returns fixture JSON by image hash, can be told to fail or hang), push service (below). All enabled only when `APP_ENV=test`. |
| **Fake push service** | A local HTTP server that accepts Web Push requests at per-subscription endpoints, verifies the VAPID JWT, decrypts the RFC 8291 payload with the test subscription's private keys, and stores it (`GET /__test/push?user=`). Can be told to answer 201, 404, 410, 429 or 500. |
| **Clock control** | All server time goes through an injected `Clock`. In test env, `POST /__test/clock {now}` sets it; Playwright uses `page.clock`. Used for use-soon, expiry, quiet hours, 48 h ask expiry, 30-day reopen, OTP expiry, session expiry. |
| **Multi-roommate fixture** | Playwright fixture `roommates(n)` opens n isolated browser contexts, each signed in as a different member on a different device profile, and exposes `expectEverywhere(fn, {within: 2000})` to assert all of them converge. |
| **Race helper** | `run_concurrently(n, fn)` (pytest + asyncio) starts n real HTTP calls against a real Postgres behind a barrier so they hit the database together; every SYNC test repeats 200 times and must pass every time. |
| **Chaos proxy** | Toxiproxy between web↔API, API↔Postgres, API↔Redis to add latency, drop connections, or cut a response after the commit. |
| **Event recorder** | Test client that subscribes to `/houses/{id}/events` and records every event, so tests can assert sequence, no gaps, no duplicates. |

---

## 3. Test catalogue

### 3.1 Money (MONEY)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| MONEY-01 | M | `compute_shares` Python passes every case in `packages/fixtures/money_cases.json` | Exact cent match |
| MONEY-02 | M | TypeScript `computeShares` passes the same fixtures | Exact cent match, identical to Python |
| MONEY-03 | M | Property test: 10,000 random bills (1–200 items, 1–20 members, weights, fees, discounts, negatives) | Σ shares = total every time; Python == TS |
| MONEY-04 | M | Largest-remainder ties break by sort_order then id | Deterministic across runs and languages |
| MONEY-05 | M | Tax/fees allocate by clipped subtotal; all-zero subtotals split equally | Matches fixtures `fees_tip_discount`, `all_zero_subtotals` |
| MONEY-06 | M | No float ever touches money (lint rule + grep test over source) | Zero violations |
| MONEY-07 | M | Overpayment after an edit becomes a credit, carried to the ledger on settle | Ledger entries equal remaining owed |
| MONEY-08 | M | Pairwise balances net across bills (Arjun +33.91, Meera +8.14, you −1.68 in apt4b) | Net +40.37 |
| MONEY-09 | M | Request add-to-split recompute (fixture `request_add_to_split`) | Kabir +383, Pranav −192, Arjun −191 cents |
| MONEY-10 | S | Bills at limits: 200 items, 20 members, $100,000.00 | Correct and under 50 ms |

### 3.2 Sign-in and sessions (AUTH)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| AUTH-01 | M | Email code: send, receive, verify, session cookie set (HttpOnly, Secure, SameSite=Lax) | Signed in |
| AUTH-02 | M | Phone code, same | Signed in |
| AUTH-03 | M | Google sign-in with PKCE via mock issuer | Signed in; `email_verified=false` rejected |
| AUTH-04 | M | Same verified email via Google and email code resolves to one user | One `users` row |
| AUTH-05 | M | Wrong code ×5 locks the code; expired code (clock +11 min) rejected | Correct errors |
| AUTH-06 | M | Send limits: 5/h per email, 3/h per phone, 10/day per IP, CAPTCHA after 2 | 429 with Retry-After |
| AUTH-07 | M | Codes never appear in logs, analytics or error reports | Log scan finds none |
| AUTH-08 | M | Session slides on use; expires after 30 idle days (clock) | 401 after expiry |
| AUTH-09 | M | Sign out everywhere revokes every session and every tab gets signed out | All tabs on sign-in screen within 2 s |
| AUTH-10 | M | CSRF: cross-origin POST without token rejected | 403 |
| AUTH-11 | M | Signing in with a method tied to another user never merges accounts | Clear error, no merge |
| AUTH-12 | M | Cannot remove your last sign-in method | 422 |
| AUTH-13 | S | Installed-app first launch with no session shows "Sign in once more on this app" | Copy and flow correct |
| AUTH-14 | M | Delete account anonymizes to "Former roommate", keeps others' amounts | Shares unchanged for others |
| AUTH-15 | M | Email codes via SMTP (Mailpit locally, Gmail settings in prod) and via the test inbox; `/api/test/inbox` requires `X-Test-Secret` (constant-time), is absent unless `EMAIL_PROVIDER=testinbox` | 404 / 401 / codes readable |
| AUTH-16 | M | Phone and Google buttons hidden unless their keys are configured (`/auth/methods`) | Only email shown on day one |

### 3.3 Houses, members, links (HOUSE, LINK)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| HOUSE-01 | M | Create house; creator is the only admin | One admin |
| HOUSE-02 | M | Add placeholders; duplicate names (case-insensitive) rejected | 409 |
| HOUSE-03 | M | Claim placeholder via invite link after sign-in | `members.user_id` set |
| HOUSE-04 | M | "I'm new" creates a member | New active member |
| HOUSE-05 | M | Admin unclaims a wrong claim; audit event written | Placeholder again |
| HOUSE-06 | M | Remove member: excluded from new bills, keeps old allocations and balances | As stated |
| HOUSE-07 | M | Leave house blocked while payer on open bills | 409 with reason |
| HOUSE-08 | M | Transfer admin keeps exactly one admin | Partial unique index holds |
| HOUSE-09 | S | Multiple houses and switcher; last-used opens | As stated |
| HOUSE-10 | M | Max 20 members per house | 422 on 21st |
| LINK-01 | M | Tokens are 128-bit random, stored hashed, constant-time compare | Code review + unit test |
| LINK-02 | M | Rotated invite link returns 410 | 410 |
| LINK-03 | M | Bill link shows read-only bill without sign-in; edits need sign-in | 401 on write |
| LINK-04 | M | Link pages send `noindex` and `no-referrer` | Headers present |
| LINK-05 | M | OG image shows store, date, total, "split N ways"; never per-person amounts or names | Image check |
| LINK-06 | M | 20 invalid tokens/h per IP → lockout | 429 |
| SET-01 | M | Save name in Settings renames your member in each house (one event per house); a taken name keeps the old one there and the answer lists the house; same name again writes nothing; only yourself (policy) | As stated |
| SET-02 | M | Settings › Add to home screen: this device's steps first (iPhone on WebKit, Android on Chromium), Install button only when `beforeinstallprompt` was captured, section hidden in standalone | As stated |
| SET-03 | M | Settings has a NOTIFICATIONS group (N2 link + email switch) and Leave house for the current house | Leave works from Settings |

### 3.4 Bills, receipts, splitting (BILL, EXTRACT, SPLIT)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| BILL-01 | M | Manual bill: add, edit, delete, reorder items | Saved |
| BILL-02 | M | Reconciliation blocks Next until items + tax + fees − discounts = total, or override with reason | Blocked / logged override |
| BILL-03 | M | Storage tag cycles FRIDGE → FREEZER → PANTRY → NOT FOOD and saves | Saved per item |
| BILL-04 | M | Send blocked while any item has no one | Button disabled with reason |
| BILL-05 | M | Send creates link, share card, stocks the kitchen, notifies members | All four happen once |
| BILL-06 | M | Draft autosave every 2 s survives reload | Nothing lost |
| BILL-07 | M | Duplicate warning: same store + date + total within 14 days | Banner shown |
| BILL-08 | M | Void with reason drops bill from balances, keeps audit | As stated |
| BILL-09 | M | Settle with outstanding moves remaining to ledger after confirm dialog | Ledger rows created |
| BILL-10 | M | Reopen within 30 days; blocked after (clock) | As stated |
| BILL-11 | M | Activity log has an entry for every state change | 1:1 with events |
| BILL-12 | S | CSV export: one row per item × member | Columns and totals correct |
| EXTRACT-01 | M | Upload JPEG/PNG/HEIC/WEBP/PDF up to 20 MB and 10 PDF pages via client upload; reject others by magic bytes; larger files refused before upload | Accepted / 415 / 413 |
| EXTRACT-02 | M | EXIF stripped (GPS gone) on device before upload; stored image has no EXIF | No EXIF |
| EXTRACT-03 | M | Accuracy on the 30 generated receipts: text PDFs >= 98% lines exact and 100% totals; images >= 85% lines and >= 95% totals | Report meets targets |
| EXTRACT-04 | M | Server recomputes totals; never trusts parser or model arithmetic | Mismatch flagged |
| EXTRACT-05 | M | Low confidence (< 0.8) rows flagged "CHECK?" | Flagged |
| EXTRACT-06 | M | Parse failure or OCR failure -> "Try again" / "Enter by hand"; file kept | As stated |
| EXTRACT-07 | M | Storage/category/icon/shelf life assigned per line; household -> none | Mapped |
| EXTRACT-08 | M | Multi-photo long receipt dedupes overlapping lines | No duplicates |
| EXTRACT-09 | M | Per-house cap of 30 extractions/day applies to the optional LLM plugin only | 429 on 31st (plugin) |
| EXTRACT-10 | M | Receipt files only via short-lived URLs to house members | 403 for others |
| EXTRACT-11 | M | Text PDF parsed server-side (pdfplumber, pypdf fallback) with line positions, multi-page | Lines and totals exact |
| EXTRACT-12 | M | Browser OCR (tesseract.js self-hosted under /ocr/, pdfjs-dist) runs on WebKit and Chromium for at least 5 image receipts, no third-party requests | Lines within targets |
| EXTRACT-13 | M | Store profiles (Instacart, Walmart, Target, Costco, Amazon/Whole Foods, ShopRite, Trader Joe's, H Mart) and edge cases (coupons, weighted items, deposits, refunds, wrapped names, $0 items) | Parsed per ground truth |
| EXTRACT-14 | M | "Upload PDF" entry point on the scan screen and drag-and-drop on desktop | Works in both themes |
| EXTRACT-15 | M | No API key needed: full suite passes with EXTRACTOR_API_KEY unset; LLM plugin enabled only when set (fake-backed test) | As stated |
| EXTRACT-16 | M | Every review screen flags each reconciliation mismatch in the generated set | 100% flagged |
| SPLIT-01 | M | Tick/untick persists within 500 ms; live totals match `compute_shares` | Exact |
| SPLIT-02 | M | Split the rest with everyone only touches unassigned items | Others unchanged |
| SPLIT-03 | M | Column header toggles a member on all items, with Undo | Works and undoes |
| SPLIT-04 | M | Undo last 10 split actions | Restores state |
| SPLIT-05 | M | Guest column; guest link; guest can tick only own column after verifying | As stated |
| SPLIT-06 | M | Member edits only own column; payer edits all | 403 otherwise |
| SPLIT-07 | M | Last person can't untick on an open bill | `LAST_PERSON` |
| SPLIT-08 | M | Share-changed badge and delta shown until viewed | Appears then clears |
| SPLIT-09 | S | Exclude a member from one bill | Column hidden |
| SPLIT-10 | M | 6+ members: columns scroll, item column sticky | Layout holds at 390 px |

### 3.5 Payments and balances (PAY, BAL)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| PAY-01 | M | Record payment → pending; payer confirms → owed drops | As stated |
| PAY-02 | M | Payer rejects → member notified, owed unchanged | As stated |
| PAY-03 | M | Payer records cash for someone → confirmed immediately | As stated |
| PAY-04 | M | Deep links: Venmo, Cash App, PayPal open with amount; Zelle shows copy | Correct URLs |
| PAY-05 | M | No handles → fallback copy | Shown |
| PAY-06 | M | Idempotent payment create (see SYNC-09) | No duplicates |
| PAY-07 | M | Reminder rate limit: one per bill per member per 48 h | Second is 429 |
| PAY-08 | S | Auto-reminders at day 3 and 7 if opted in (clock) | Sent once each |
| BAL-01 | M | Tabs screen shows net +40.37 and pair rows for apt4b | Exact |
| BAL-02 | M | Balances update on every device after a payment within 2 s | Converged |
| BAL-03 | M | Voided bills excluded; chip-ins included | As stated |
| BAL-04 | M | "ALL SQUARE" when net is zero | Shown |

### 3.6 Fridge and pantry (KITCH, ASK)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| KITCH-01 | M | Sending a bill stocks one item per consumable line; NOT FOOD never stocked | Counts match |
| KITCH-02 | M | Owners equal allocation members, guests excluded | Exact |
| KITCH-03 | M | Ticking someone on an open bill adds them as owner; unticking removes | Same transaction |
| KITCH-04 | M | Deleting a line or voiding a bill removes its items; settling changes nothing | As stated |
| KITCH-05 | M | Fridge shows Freezer and Refrigerated only; Pantry shows one area | Layout |
| KITCH-06 | M | Kitchen panels show only USE SOON items and counts; no filters there | As stated |
| KITCH-07 | M | Filters ALL / MINE / OPEN / USE SOON with correct counts; non-matching dim to 22% | Exact |
| KITCH-08 | M | OPEN only when every owner is open; sheet names holdouts | As stated |
| KITCH-09 | M | Sharing default from settings applies to new items | As stated |
| KITCH-10 | M | Use soon at 80% of shelf life, "Still there?" at 100%, "Still good" +3 days (clock) | Flags flip at the right times in house tz |
| KITCH-11 | M | Items never auto-removed | Still present at 300% |
| KITCH-12 | M | Used up with 5 s Undo; non-owners can't | 403 for non-owners |
| KITCH-13 | S | Add leftovers (no bill, no price) | Created; only free answers allowed |
| KITCH-14 | M | Owner leaves house → dropped from owners; no owners → open to all | As stated |
| KITCH-15 | M | Daily use-soon round-up at 9:00 house time, once per state change | One push |
| KITCH-16 | M | 60+ items in an area: virtualized, everything reachable | Scroll test |
| ASK-01 | M | Non-owner asks a locked item with amount and note | Pending, all owners notified |
| ASK-02 | M | Owners can't ask own items; open items show Help yourself | No ask button |
| ASK-03 | M | On the house → grant; requester sees open for them only | As stated |
| ASK-04 | M | Add to split on open bill → allocation added, shares exact, requester becomes owner | Matches MONEY-09 |
| ASK-05 | M | Bill settled → Chip in prefilled with would-be share, editable $0.01..line total, ledger entries | As stated |
| ASK-06 | M | Decline shows "Not this time", no reason | As stated |
| ASK-07 | M | 48 h expiry (clock) | Expired + requester told |
| ASK-08 | M | Requester cancels while pending | Cancelled |
| ASK-09 | M | Owner undo within 5 min (incl. removing split allocation if bill open) | Restored |
| ASK-10 | M | Limits: 1 pending per item, 3 per 7 days, 10 outgoing | 429 / 409 |
| ASK-11 | M | Manual leftovers: only On the house / decline | Split option absent |
| ASK-12 | M | Requester leaves house → pending asks cancelled | As stated |
| ASK-13 | M | Requests inbox tabs and counts; Kitchen badge count | Exact |
| ASK-14 | M | Every arrow in the PRD §19.4 request diagram has a test | Coverage map complete |

### 3.7 Multi-user sync (SYNC)

SYNC-01 … SYNC-24 are the rows of PRD §21.7 and are all **Must**. Each runs at two levels:

1. **API race test** with `run_concurrently` against real Postgres and Redis, 200 iterations, asserting
   the final database state, the event log (sequence gapless, no duplicate events, correct order) and
   the share invariant after every commit.
2. **Multi-roommate browser test** with `roommates(n)` asserting what each person sees, within 2 s.

Additional sync tests:

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| SYNC-25 | M | Event sequence is gapless and equals commit order under 50 concurrent writers to one house | Recorder sees 1..N with no gaps |
| SYNC-26 | M | No event is published for a rolled-back transaction | Recorder sees none |
| SYNC-27 | M | `Last-Event-ID` replay after 500+ events or 7+ days returns `resync` | Client refetches |
| SYNC-28 | M | Missing `If-Match` on a guarded edit → 428; missing `Idempotency-Key` → 400 | As stated |
| SYNC-29 | M | Same idempotency key with a different body → 422 `IDEMPOTENCY_MISMATCH` | As stated |
| SYNC-30 | M | Rapid tick-untick-tick within 250 ms sends one final write | One request, final state on |
| SYNC-31 | M | Two tabs elect one SSE leader; closing the leader hands over within 3 s | One stream at a time |
| SYNC-32 | M | Client never flickers own optimistic changes when its echo event arrives | Visual check in test |
| SYNC-33 | M | Removed member's open stream gets `revoked` within 5 min (clock) | Closed |
| SYNC-34 | M | Polling fallback kicks in after 3 failed SSE connects and stops when SSE recovers | As stated |
| SYNC-35 | M | A read racing a committed write sees one consistent state (bill, items, allocations from one snapshot) | 200, Σ shares = total; never a 500 |

### 3.7b Vercel platform (VERCEL)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| VERCEL-01 | M | SSE stream from Postgres closes at 270 s; reconnect with Last-Event-ID loses and duplicates nothing | Recorder sees 1..N |
| VERCEL-02 | M | `PROFILE=vercel` (no Redis) delivers events to every device within 2 s | Converged |
| VERCEL-03 | M | Request bodies over 4.5 MB rejected with 413 before any work; receipts use client upload | 413 |
| VERCEL-04 | M | Outbox drained via the Queues SDK (embedded/devserver); delayed messages for quiet hours; sweeper drains leftovers | All delivered once |
| VERCEL-05 | M | Rate limits and idempotency work with Postgres only | 429 / replay |
| VERCEL-06 | M | Alembic never runs from a request; `make deploy` runs it against the direct URL | Static check + script test |
| VERCEL-07 | M | `vercel.json` routes `/api/*` to FastAPI and everything else to Next.js; same-origin cookies | launch-check |
| VERCEL-08 | M | Python bundle size under 500 MB; no OCR/image libraries in the API | Size check |
| VERCEL-09 | M | Daily cron endpoint is idempotent and runs use-soon, retention and export | As stated |
| VERCEL-10 | M | Morning scripts (deploy-setup, deploy, launch-check, set-email, redeploy, rollback, prod-clean-test-data) pass dry-run and stubbed-CLI tests incl. failures | All pass |
| VERCEL-11 | M | `PROFILE=vercel` API engine keeps `DB_SERVERLESS_POOL_SIZE` connections (pre-ping, recycle) and never caches prepared statements; 0 and the queue subscriber use no pool (ADR-0032) | Static + engine check |

### 3.8 Install and offline (PWA, OFFLINE)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| PWA-01 | M | Manifest valid (Lighthouse PWA audit and a schema check): name, id, start_url, standalone, icons incl. maskable, shortcuts, screenshots | Passes |
| PWA-02 | M | Service worker registers, precaches shell, serves offline page | Offline load works |
| PWA-03 | M | API reads are network-first with 3 s fallback to cache, marked stale | Banner shows |
| PWA-04 | M | Auth, mutations, SSE, receipt images never cached | Cache inspection |
| PWA-05 | M | Chromium: `beforeinstallprompt` captured; card appears after first bill or 2nd visit; max twice | As stated |
| PWA-06 | M | iPhone (WebKit, standalone false): N1 shows 3 install steps; push not offered in tab | As stated |
| PWA-07 | M | Standalone launch detected; `pwa_installed` logged once | As stated |
| PWA-08 | M | Update flow: new worker waits; toast; refresh only on tap; never during an open form | As stated |
| PWA-09 | M | `426 STALE_CLIENT` forces the refresh prompt | As stated |
| PWA-10 | M | Theme color follows light/dark | Meta tags correct |
| PWA-11 | S | App shortcuts open the right screens | Routes correct |
| PWA-12 | M | Lighthouse mobile ≥ 90 performance, accessibility, best practices | Scores |
| OFFLINE-01 | M | Offline: cached screens read-only, banner, all writes disabled with reason | As stated |
| OFFLINE-02 | M | Back online: catch-up fetch, banner clears, no lost or duplicate writes | Converged |
| OFFLINE-03 | M | Write that times out retries 3× with same key, then shows Retry | No duplicates |
| OFFLINE-04 | M | Nothing is queued while offline | Queue empty |

### 3.9 Notifications (PUSH, EMAIL)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| PUSH-01 | M | Pre-prompt never on first load; appears after first bill, first ask, or from Settings | As stated |
| PUSH-02 | M | Permission requested only from a tap; denied → never asked again on that device | As stated |
| PUSH-03 | M | Subscription saved with device label; one user many devices | Rows correct |
| PUSH-04 | M | Subscription change re-registers (simulate `pushsubscriptionchange` and changed endpoint on start) | Updated |
| PUSH-05 | M | Fake push 404/410 deletes the subscription; 5 other failures mark stale | As stated |
| PUSH-06 | M | Every event type in PRD §20.2 sends the right title, body, URL and tag to the right people | Payload snapshot per type |
| PUSH-07 | M | The actor is never notified of their own action on any device | No payload |
| PUSH-08 | M | Private on the lock screen hides names, items and amounts | Payload check |
| PUSH-09 | M | Preferences matrix respected per type × channel | Only enabled channels |
| PUSH-10 | M | Quiet hours hold and group until 08:00 user time (clock) | Delivered once at 08:00 |
| PUSH-11 | M | Share-changed coalesces per bill per member over 10 min (latest net change) | One push |
| PUSH-12 | M | Daily cap 20 per user; overflow goes to inbox only | As stated |
| PUSH-13 | M | Dedupe key prevents duplicates when the worker retries | One notification row |
| PUSH-14 | M | No notification for a rolled-back change (outbox in same transaction) | None |
| PUSH-15 | M | Service worker shows notification from payload; `notificationclick` focuses an open window and navigates, else opens one | Playwright SW test |
| PUSH-16 | M | App badge equals pending asks + unseen changes, clears when seen | `setAppBadge` calls |
| PUSH-17 | M | `POST /push/test` reaches every device of the caller | Payloads received |
| PUSH-18 | M | Payload ≤ 2 KB, encrypted, VAPID JWT valid, TTL and urgency per type | Fake service verifies |
| PUSH-19 | M | Drains running at once on several instances never deadlock, fail or send a notification twice | Every device gets each push once |
| EMAIL-01 | M | Emails only to verified addresses with List-Unsubscribe and footer address | Mailpit check |
| EMAIL-02 | M | Unsubscribe link turns off that type for email only | Pref updated |
| EMAIL-03 | M | Weekly use-soon digest only when opted in, Sunday 10:00 house time | Clock test |
| EMAIL-04 | M | SPF/DKIM/DMARC configured on the sending domain | DNS check in release |
| EMAIL-05 | S | Email renders in light and dark clients | Screenshot check |

### 3.10 Security (SEC)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| SEC-01 | M | Authorization matrix: every endpoint × role (admin, payer, member, guest, non-member, signed out) | Generated test; only allowed cells succeed |
| SEC-02 | M | IDOR: IDs from another house are rejected everywhere | 404/403 |
| SEC-03 | M | CSP blocks inline scripts; no `unsafe-inline`; HSTS on | Headers |
| SEC-04 | M | Uploads served from a separate origin with `Content-Disposition: attachment` | Headers |
| SEC-05 | M | Rate limits on writes (60/min/user) and reads (300/min/IP) | 429 |
| SEC-06 | M | SQL injection and XSS probes on every text field (names, notes, store, item) | Escaped / rejected |
| SEC-07 | M | Secrets never in logs or client bundle | Scan clean |
| SEC-08 | M | ZAP baseline: no high or medium findings | Report |
| SEC-09 | M | Dependency audit (pip-audit, pnpm audit): no high/critical | Clean |
| SEC-10 | M | Push endpoints and keys only readable by their owner | 403 |
| SEC-11 | M | `/__test/*` routes don't exist outside test env | 404 in prod build |
| SEC-12 | M | Session fixation: new session id on sign-in | Changed |
| SEC-13 | M | Account deletion removes push subscriptions, sessions, identities | Gone |
| SEC-14 | S | Brute force on bill tokens throttled (LINK-06) | 429 |

### 3.11 Accessibility, visual, copy (A11Y, VIS, COPY)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| A11Y-01 | M | axe: zero serious/critical on every screen, light and dark | Clean |
| A11Y-02 | M | Keyboard only: every journey completable; focus visible; sheets trap and return focus | Pass |
| A11Y-03 | M | Split grid roving tabindex with arrow keys; Space toggles | Pass |
| A11Y-04 | M | Every icon-only button has a label; checkboxes announce "{Name} uses {Item}" | Pass |
| A11Y-05 | M | Contrast 4.5:1 text, 3:1 large and UI in both themes | Automated check |
| A11Y-06 | M | 44×44 px minimum targets | Automated measure |
| A11Y-07 | M | `prefers-reduced-motion` turns off every animation | Pass |
| A11Y-08 | S | VoiceOver (iPhone) and TalkBack (Android) manual pass of main journeys | Notes in report |
| VIS-01 | M | Screenshot diff of every mockup screen at 390 px, light and dark, vs approved baselines | Within threshold |
| VIS-02 | M | 320 px and 1024 px layouts hold (no horizontal scroll, Kitchen panels side by side at ≥ 1024) | Pass |
| VIS-03 | M | No visible scrollbars inside panels; long screens scroll as a page | Pass |
| VIS-04 | S | Food icons render for every `icon_key` | Gallery page check |
| COPY-01 | M | All copy matches PRD §14A/§19.8/§20.2 strings (string catalog snapshot) | Match |
| COPY-02 | M | Amounts always `$0.00` with tabular figures; dates per `Intl` | Pass |

### 3.12 Performance, resilience, data (PERF, CHAOS, DATA)

| ID | P | Test | Pass when |
| --- | --- | --- | --- |
| PERF-01 | M | k6: 200 concurrent users editing 50 bills | p95 API < 300 ms, 0 errors |
| PERF-02 | M | k6: 1,000 open SSE streams across 200 houses | Event delivery p95 < 2 s |
| PERF-03 | M | Bill link page LCP < 2.5 s, INP < 200 ms, CLS < 0.1 on throttled mobile | Lighthouse |
| PERF-04 | M | Link page JS < 150 KB gzipped | Bundle check |
| PERF-05 | M | Extraction p90 < 15 s | Timing report |
| PERF-06 | S | Kitchen with 200 items renders < 1 s on mid-range device profile | Timing |
| CHAOS-01 | M | Response cut after commit (Toxiproxy) → retry returns original | SYNC-16 holds |
| CHAOS-02 | M | Redis down 60 s → writes succeed, clients catch up by gap fetch/polling | Converged |
| CHAOS-03 | M | Postgres failover/restart mid-write → no partial writes | Invariants hold |
| CHAOS-04 | M | Worker crash mid-send → outbox retries, no duplicate pushes (dedupe) | One delivery |
| CHAOS-05 | M | Push service 500/429 → backoff schedule followed | Timings |
| CHAOS-06 | M | 2,000 ms latency web↔API → UI shows Saving…, no double submits | Pass |
| CHAOS-07 | M | Extractor hangs → 30 s timeout, manual entry offered | Pass |
| CHAOS-08 | S | Clock skew of ±10 min on a client → relative times still correct | Uses server offset |
| DATA-01 | M | Every Alembic migration upgrades and downgrades on a seeded database | Pass |
| DATA-02 | M | Backup + restore drill: restored DB passes invariant checks | Pass |
| DATA-03 | M | Retention jobs purge OTPs (24 h), events (7 d), idempotency keys (24 h), receipt images (2 y after settle) | Clock test |
| DATA-04 | M | Export my data returns complete JSON | Schema check |
| DATA-05 | M | Invariant checker script (`scripts/check_invariants.py`) finds zero violations on seeded and load-test data | Clean |
| DATA-06 | M | Every state change has exactly one `house_events` row and one activity entry | 1:1 |

---

## 4. End-to-end journeys (run on WebKit iPhone, Chromium Pixel, Firefox desktop)

| ID | Journey |
| --- | --- |
| E2E-01 | New payer: sign in with email code → create house → add 3 roommates → share invite → snap receipt (fake extractor) → fix a flagged line → tag storage → split → send → share card |
| E2E-02 | Roommate: open bill link signed out → read-only → claim name → phone code → tick own items → see share change on payer's screen within 2 s |
| E2E-03 | Payments: member pays via deep link (stubbed) → marks paid → payer confirms → balances update everywhere |
| E2E-04 | Kitchen: open Fridge → filter MINE → open locked item → ask → owner (second browser) gets push (fake) and answers Add to split → shares update → requester now owner |
| E2E-05 | Use soon: advance clock → Kitchen shows USE SOON → push round-up at 9:00 → owner taps Used up → Undo |
| E2E-06 | Settle: payer settles with outstanding → ledger carryover → reopen within 30 days → settle again |
| E2E-07 | Install + push: Chromium installs (prompt), enables notifications, receives each event type from another roommate's actions |
| E2E-08 | Offline: go offline mid-session → read-only → back online → catch-up |
| E2E-09 | Multi-device same user: phone + laptop + second tab stay in sync, sign out everywhere |
| E2E-10 | Conflict: two payer devices edit the same price → merge prompt → re-apply |

---

## 5. Manual device QA (real hardware, before every release)

Record device, OS, browser and result for each line in `docs/TEST_REPORT.md`.

1. **iPhone (latest iOS and one version back), Safari:** open invite link → claim → Add to Home Screen → open from Home Screen → confirm whether a second sign-in is needed → enable notifications from N1 → from another device trigger every event type → each notification arrives, tapping opens the right screen, badge count correct → lock-screen privacy on/off → quiet hours hold overnight (set quiet hours to the next 5 minutes) → remove and re-add after denying permission.
2. **iPhone, Chrome and Edge:** Add to Home Screen works and push works from the installed app.
3. **Android (Pixel or Samsung), Chrome:** install via prompt → push in browser tab and installed app → app shortcuts → badge.
4. **Desktop Chrome/Edge (macOS, Windows), Firefox, Safari (Add to Dock):** install where supported, push, multi-tab leader election.
5. **Airplane mode** mid-tick and mid-payment on iPhone and Android: no duplicates, clear states.
6. **Two real phones side by side** ticking the same bill for 2 minutes: always converge; no flicker.
7. **VoiceOver and TalkBack** through E2E-01, E2E-02, E2E-04.
8. **Dark mode** on every screen on each device.

## 6. Exploratory charters (60 minutes each, notes in the report)

1. Try to make the money wrong: weird receipts, negative lines, coupons, last-cent splits, edits after payment.
2. Try to see or change another house's data with links, IDs and old sessions.
3. Abuse timing: background the app for hours, change time zones, travel across midnight, quiet hours edges.
4. Hammer sync: 4 devices, flaky Wi-Fi, rapid taps, settle while others edit.
5. Notification annoyance audit: count every push a typical member gets in a simulated week; flag anything noisy.

---

## 7. Exit criteria (release gate)

- [ ] Every **Must** test passes on CI three runs in a row (no flaky retries hiding failures).
- [ ] SYNC suite passes 200/200 iterations per test.
- [ ] `check_traceability.py`: every FR-xx has ≥ 1 test; every row of PRD §15, §19.9 and §21.7 has a test.
- [ ] Coverage: 100% branches on `money.py`, `money.ts`, `policy.py`; ≥ 90% lines on backend services; ≥ 80% on frontend `lib/`.
- [ ] Golden-set extraction ≥ 90%.
- [ ] Lighthouse ≥ 90 (performance, accessibility, best practices); PWA audit passes.
- [ ] Zero high/critical security findings.
- [ ] Manual device QA (section 5) complete with no open Must issues.
- [ ] `docs/TEST_REPORT.md` filled in using the template below and committed.

## 8. `docs/TEST_REPORT.md` template

```markdown
# KitchenTab test report — <version> — <date>

## Summary
Pass: N · Fail: N · Skipped/waived: N · Flaky (quarantined): N

## By area
| Area | Total | Pass | Fail | Waived | Notes |
| --- | --- | --- | --- | --- | --- |
| MONEY | | | | | |
| … | | | | | |

## Failures and waivers
| ID | What failed | Cause | Fix / waiver reason | Owner |

## Coverage
money.py / money.ts / policy.py branches · backend lines · frontend lib lines

## Device QA
| Device | OS | Browser | Install | Push | Sync | Notes |

## Known issues shipped
```
