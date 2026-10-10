# Instructions for AI coding assistants

The rules for this repository live in **`CLAUDE.md`**. Read it first and follow it exactly, whatever
assistant you are (Claude Code, Codex, Cursor, Copilot, …). Then read `docs/PROGRESS.md` for the
current state and `CONTRIBUTING.md` for the team workflow.

The short version:

- `main` is production (https://kitchentab.vercel.app, real households). Work on a branch, open a PR,
  keep CI green. Never push straight to `main`.
- Money is integer cents everywhere; authorization lives only in `apps/api/app/policy.py`; every write
  is one transaction ending in a house event; notifications go through the outbox.
- Tests first for money, policy, auth, sync and state transitions. Every test carries its TEST_PLAN ID
  (`scripts/check_traceability.py` must stay green). Fix failures at the root; never weaken or skip tests.
- Schema changes are Alembic migrations, additive and backwards compatible; production is migrated with
  `make migrate-prod` before the code that needs it is merged.
- Never commit secrets (`.env*`, `.vercel/`, tokens, passwords). Never call real email, SMS, OAuth, LLM
  or push services in tests: use the fakes. Keep local servers on localhost.
- Commit messages carry FR / TEST IDs, e.g. `feat(kitchen): custom split on asks (FR-72, ASK-15)`.
