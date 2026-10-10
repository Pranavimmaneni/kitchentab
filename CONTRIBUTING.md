# Contributing to KitchenTab

## Setup

1. Install Docker Desktop, Node 24 with pnpm 9 (`corepack enable`), Python 3.12 with
   [uv](https://docs.astral.sh/uv/), and `make` (on Windows, run the commands from Git Bash).
2. `make install`, then `make dev`. The app is at http://localhost:3000; sign-in codes are in
   Mailpit (http://localhost:8025). `make seed SCENARIO=apt4b` loads a demo house.
3. Everything listens on localhost only. No real email, push, storage or OAuth account is needed: the
   fakes in `infra/` and `apps/api/fakeservices/` stand in for them (TEST_PLAN §2).

## Workflow

1. Pick an item from `docs/PROGRESS.md` (or agree a new feature with the team and add it there first).
2. Branch from `main`: `feat/<short-name>` or `fix/<short-name>`.
3. Read the PRD sections it touches. New behaviour gets FR IDs in `docs/PRD.md` and test IDs in
   `docs/TEST_PLAN.md`; non-obvious choices get an ADR in `docs/decisions/`.
4. Tests first for money, policy, auth, sync and state transitions. Keep the commands below green.
5. Open a pull request. CI (`.github/workflows/ci.yml`) runs lint, unit/integration tests, the sync
   suite in both profiles and the browser tests. Get one review from a teammate.
6. Merge to `main`. Vercel deploys `main` to production automatically (about 2 minutes).
7. Update `docs/PROGRESS.md` in the same PR.

## Commands

| Command | What |
| --- | --- |
| `make lint` | ruff, mypy, eslint, tsc |
| `make test` | API tests (Testcontainers Postgres/Redis) + web unit tests |
| `make test-sync` | concurrency / race suite (`PROFILE=vercel` for the serverless profile) |
| `make e2e`, `make e2e-multi` | Playwright on WebKit (iPhone), Chromium (Pixel), Firefox |
| `make test-push`, `make test-extract`, `make test-security`, `make test-chaos`, `make test-load` | focused suites |
| `make house` | production build on this computer only (http://localhost:3100) |
| `make simulate SEED=1 MINUTES=10` | four roommates on six devices hammering one house, with invariant checks |
| `make check-traceability` | every TEST_PLAN / FR id has a test |
| `make gen` | regenerate the OpenAPI schema and the TypeScript client after API changes |

## Database changes

- Only through Alembic migrations in `apps/api/migrations/versions/` (never edit an applied one).
- Make them **additive and backwards compatible**: the old code must keep working on the new schema
  (add columns/tables first; remove or rename only after the code no longer uses them).
- Vercel's Git deploys ship code only. The project owner runs **`make migrate-prod`** before merging a
  PR that needs a migration. Say so in the PR description.

## Deploys and secrets

- `main` = production (https://kitchentab.vercel.app). Pull-request branches get Vercel preview URLs once
  Preview environment variables are set up (see `docs/LAUNCH.md`).
- Production settings and secrets live in the Vercel project (owner access). Never commit `.env*`,
  `.vercel/`, tokens or passwords; `.env.example` lists every variable with safe local defaults.
- Ops scripts (`make deploy-setup`, `make redeploy`, `make set-email`, `make migrate-prod`,
  `make rollback`) need the Vercel CLI logged in to the owner's account; see `docs/LAUNCH.md`.
- If production breaks: `make rollback` (or Vercel dashboard → Deployments → Promote the previous one).

## Conventions

- Money: integer cents only; `compute_shares` exists once per language and must pass
  `packages/fixtures/money_cases.json`.
- Authorization only in `apps/api/app/policy.py`; no auth logic in routes or UI.
- UI: design tokens from `apps/web/styles/tokens.css`, both themes, WCAG 2.2 AA, 44 px targets, copy in
  `apps/web/lib/strings.ts`.
- Commit messages: `type(scope): summary (FR-xx, TEST-ID)`.
