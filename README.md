# KitchenTab

Split grocery receipts item by item with your roommates, then see every food item in the shared
fridge and pantry: who owns it, who will share, and ask-to-share requests. An installable web app
(PWA) for phones and laptops, used by several people at once with live updates.

**Live:** https://kitchentab.vercel.app — real households use it, so `main` is production.

## Start here (people and AI assistants)

1. Read **`CLAUDE.md`**: the project rules (money in integer cents, authorization only in
   `policy.py`, one transaction per write, the four write kinds, outbox notifications, tests with
   IDs). Every assistant follows it; `AGENTS.md` points other tools there.
2. Read **`docs/PROGRESS.md`**: what's built, what's open, and the next features in order.
3. Read **`CONTRIBUTING.md`**: local setup, the branch → PR → `main` flow, migrations and deploys.
4. Look things up in **`docs/PRD.md`** (the spec), **`docs/TEST_PLAN.md`** (every test by ID),
   **`docs/decisions/`** (ADRs: why things are the way they are) and **`docs/design/`** (screens).

## What's in the repo

| Path | What |
| --- | --- |
| `apps/web` | Next.js 15 + React 19 PWA (TanStack Query, Tailwind 4, Serwist service worker, tesseract.js OCR) |
| `apps/api` | FastAPI + SQLAlchemy 2 (async) + Alembic on Postgres; notifications outbox, receipts, sync stream |
| `packages/fixtures` | Golden money cases and receipt fixtures shared by both apps' tests |
| `scripts/` | Dev and ops tools: `house.py` (local production-like run), `simulate_household.py`, `load_test.py`, `test_report.py`, `deploy/` (Vercel) |
| `infra/` | docker compose for the local stack (Postgres, Redis, MinIO, Mailpit, fakes), all on localhost |
| `docs/` | PRD, test plan, ADRs, design mockups, launch notes, progress |
| `vercel.json` | Two Vercel Services: `web` (Next.js) and `api` (FastAPI), `/api/*` routed to the API |

## Quick start

Needs Docker, Node 24 + pnpm 9, Python 3.12 + uv, and `make` (Git Bash on Windows).

```bash
make install        # pnpm + uv dependencies, Playwright browsers
make dev            # docker stack + API (8000) + worker + web (3000)
make seed SCENARIO=apt4b
```

Open http://localhost:3000. Sign-in codes land in Mailpit at http://localhost:8025. No external
account is needed locally: email, push, storage, OAuth and SMS all have local fakes.

`make house` runs the production build on this computer only (http://localhost:3100), closest to
what Vercel runs. See `CONTRIBUTING.md` for tests and the full command list.

## History

The app was built milestone by milestone from `docs/PRD.md`; `docs/BUILD_ALL.md` and
`docs/history/KICKOFF_PROMPT.md` are the original build instructions, kept for context only.
