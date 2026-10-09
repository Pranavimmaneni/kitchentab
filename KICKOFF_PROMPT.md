# Prompts for Claude Code

Paste these in order. Review Claude Code's reply at every stop.

## 1. Kickoff (paste first, from the repo root)

```
You're building KitchenTab: an installable web app (PWA) where roommates split grocery receipts item by item, then see every food item in a shared, illustrated fridge and pantry with who owns it, who will share, and ask-to-share requests. Several people use it at once on phones and laptops, it installs to the home screen, and it sends push notifications. Everything you need is in this repo:

- CLAUDE.md — working rules (read first, follow always)
- docs/PRD.md — the full spec and build contract (22 sections)
- docs/TEST_PLAN.md — every test you must write and pass, with IDs and the release gate
- docs/design/ — approved mockups (read docs/design/README.md for the format)
- docs/reference/compute_shares_reference.py + packages/fixtures/money_cases.json — executable money spec

Do this now, in order:
1. Read CLAUDE.md, all of docs/PRD.md, all of docs/TEST_PLAN.md, then docs/design/README.md, and skim every mockup.
2. Before writing code, reply with: (a) a one-paragraph summary of the product in your own words; (b) contradictions, gaps or risks you found in the PRD or test plan, as a numbered list; (c) your plan for Milestone 1 as a checklist mapped to PRD sections and TEST_PLAN IDs. Then stop and wait for my go-ahead.
3. After I approve, build Milestone 1 (Foundation, PRD §17): monorepo with pnpm + uv; docker-compose (Postgres 16, Redis, MinIO, Mailpit, mock OIDC, fake push service); Makefile with every target in CLAUDE.md; GitHub Actions CI; FastAPI skeleton with health check and settings validation; Alembic baseline with every table and constraint in PRD §10, §19.7, §20.2 and §21.8; Next.js app with manifest stub, design tokens and light/dark/system theming; the base components from §14B on /dev/components; the harnesses from TEST_PLAN §2 (scenario seeder, fakes, clock control, multi-roommate fixture, race helper, event recorder); scripts/check_traceability.py; and docs/PROGRESS.md with all 10 milestones as checklists.
4. Finish Milestone 1 by running make lint test, showing results and /dev/components in both themes, then stop for review.

Rules: follow CLAUDE.md strictly. Ask before adding dependencies not named in PRD §12/§20 or changing anything in PRD §6 (money), §7 (auth) or §21 (sync). Put FR and TEST_PLAN IDs in commit messages.
```

## 2. Each later milestone

Replace `N` and the name; paste after reviewing the previous milestone.

```
Start Milestone N (<name>) from PRD §17.
1. Re-read CLAUDE.md, the PRD sections this milestone touches, the matching TEST_PLAN sections, and the matching mockups in docs/design/.
2. Reply with the FR IDs, TEST_PLAN IDs and edge cases (§15, §19.9, §21.7) in scope, plus a checklist plan. Flag anything ambiguous. Then continue unless something blocks you.
3. Build it test-first for money, policy, auth, sync and state changes. Follow the four write kinds in §21.3 on every endpoint. Match §14A/§19.8/§20 screen specs exactly in both themes.
4. Finish with make lint test test-sync e2e e2e-multi green (plus test-push from Milestone 9), docs/PROGRESS.md updated (including assumptions), and a short list of things for me to try by hand. Then stop for review.
```

Milestones (PRD §17): 1 Foundation · 2 Money core · 3 Auth and houses · 4 Bills without OCR · 5 Receipt extraction · 6 Real-time sync and open-bill life · 7 Tabs and balances · 8 Fridge and pantry · 9 Install and notifications · 10 Hardening and full verification.

## 3. Useful follow-ups

- **Review pass:** `Review Milestone N against PRD sections <x, y>, TEST_PLAN sections <a, b> and CLAUDE.md. List every gap or bug with file and line, then fix them.`
- **Visual check:** `Run the app, open <route> at 390 px in light and dark, compare with docs/design/<File>.dc.html, and fix every difference in spacing, type, color and copy.`
- **Sync audit:** `Run make test-sync with 500 iterations and make e2e-multi. For any failure, explain the race, fix it at the source (not with retries or sleeps), and rerun.`
- **Money audit:** `Prove Python and TS compute_shares match packages/fixtures/money_cases.json and pass the 10,000-bill property test. Show results.`

## 4. Final verification (paste after Milestone 10 is built)

```
The app is built. Now verify it with no stone unturned.

1. Re-read docs/TEST_PLAN.md in full. List any test ID that doesn't exist yet in the codebase, write those tests, and confirm scripts/check_traceability.py reports every FR-xx, every §15/§19.9 edge case and every §21.7 race covered.
2. Run make test-all. It must run: unit, integration, SYNC suite at 200 iterations per test, Playwright on WebKit iPhone + Chromium Pixel + Firefox desktop, multi-roommate journeys E2E-01 to E2E-10, push pipeline against the fake push service, chaos via Toxiproxy, k6 load, security (authorization matrix, ZAP baseline, dependency audits), axe on every screen in both themes, visual diffs against baselines, Lighthouse with the PWA audit, migrations up/down, backup-restore drill, retention jobs and the invariant checker.
3. Fix every failure at its root cause. Don't weaken assertions, add sleeps, raise timeouts or mark tests skipped to get green; if you believe a test is wrong, stop and explain why to me first.
4. Run the full suite three times in a row; anything that fails once is flaky: find the cause and fix it.
5. Fill in docs/TEST_REPORT.md using the template in TEST_PLAN §8, including coverage numbers and every waiver with its reason.
6. Write docs/DEVICE_QA.md as a step-by-step checklist from TEST_PLAN §5 that I can follow on my iPhone, an Android phone and a laptop, with the exact URLs, test accounts and what I should see at each step.
7. Reply with: the summary table from the report, anything that failed or was waived, and the three riskiest areas you'd want a human to check by hand.
```
