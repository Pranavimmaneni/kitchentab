# KitchenTab — rules for Claude Code

KitchenTab is an installable web app (PWA) for a shared household: split grocery receipts item by
item, then see every food item in a shared fridge and pantry with who owns it, who will share, and
ask-to-share requests. Several roommates use it at the same time on phones and laptops.
The full spec is `docs/PRD.md`. It is the contract: read the relevant sections before starting any
milestone, and do not deviate without asking.

## Where things are
- `docs/PRD.md` — requirements (FR-xx), money (§6), auth (§7), API (§11), stack (§12), screens (§14A), components (§14B), edge cases (§15), build plan (§17), fridge and pantry (§19), install + push (§20), multi-user sync (§21), testing (§22)
- `docs/TEST_PLAN.md` — every test with an ID, the harnesses, device QA and the release gate
- `docs/design/*.dc.html` — approved mockups; read `docs/design/README.md` first
- `docs/reference/compute_shares_reference.py` + `packages/fixtures/money_cases.json` — executable money spec
- `docs/decisions/` — ADRs, one per non-obvious choice
- `docs/PROGRESS.md` — milestone checklist you keep current; `docs/TEST_REPORT.md` — final report

## Commands (keep these working; update this list when they change)
- `make dev` — docker-compose (Postgres, Redis, MinIO, Mailpit, fake push, mock OIDC) + API + worker + web
- `make test` · `make test-sync` · `make e2e` · `make e2e-multi` · `make test-push` · `make test-chaos` · `make test-load` · `make test-security` · `make test-a11y` · `make test-visual` · `make golden` · `make test-all`
- `make lint` — ruff, mypy, eslint, tsc · `make gen` — OpenAPI + TS client · `make seed SCENARIO=apt4b`
- `make test-extract` · `make restore-drill` · `make check-invariants` · `make check-traceability` · `make simulate SEED=1 MINUTES=30` (live household simulation) · `python scripts/test_report.py` (writes docs/TEST_REPORT.md from `.cache/results/`)
- `make local-vercel-up` / `-down` / `-status` (https://localhost:8443 rehearsal) · `make launch-check TARGET=local-vercel` · morning deploy: `make deploy-setup deploy redeploy rollback set-email prod-clean-test-data` (`DRY_RUN=1`; never run against the real account from here)

## Non-negotiable rules
1. **Money is integer cents everywhere.** `compute_shares` exists once per language and must pass `packages/fixtures/money_cases.json`.
2. **Authorization only in `apps/api/app/policy.py`.** No auth logic in routes or UI.
3. **Every write is one transaction** that ends by taking the next `houses.event_seq`, inserting a `house_events` row and any `outbox` rows (PRD §21.2). Publish to Redis only after commit.
4. **Use the four write kinds from PRD §21.3** on every endpoint: set-state `PUT` (no toggles), guarded `PATCH` with `If-Match`, state transitions with row locks + `Idempotency-Key`, creates with `Idempotency-Key` + `X-Client-Mutation-Id`.
5. **Optimistic UI only for set-state writes.** Never queue writes offline. Never compute authoritative shares, owners or balances on the client.
6. **Notifications go through the outbox** with dedupe keys; never send a push from a request handler; never notify the actor.
7. **Never call real SMS, email, OAuth, LLM or push services in tests.** Use the fakes (TEST_PLAN §2).
8. Never log secrets, codes, tokens, push keys, full emails or phone numbers.
9. Design tokens only from `apps/web/styles/tokens.css`; both themes; WCAG 2.2 AA; 44 px targets; no scroll panels inside screens.
10. Every test carries its TEST_PLAN ID and the FR IDs it covers; `scripts/check_traceability.py` must stay green.

## How to work
- One milestone at a time (PRD §17, 10 milestones). Start by restating scope, FR IDs and TEST_PLAN IDs in scope. End with `make lint test test-sync e2e` green, `docs/PROGRESS.md` updated, and stop for review.
- Tests first for money, policy, auth, sync and state transitions.
- Commit messages carry FR IDs: `feat(sync): gapless house event sequence (FR-62, SYNC-25)`.
- Ask before: adding a dependency not named in PRD §12/§20, changing the schema outside a migration, changing PRD §6, §7 or §21 rules, or skipping a test. Record decisions as ADRs.
- When the PRD is silent, choose the simplest option consistent with it, note it under "Assumptions" in `docs/PROGRESS.md`, and continue.
