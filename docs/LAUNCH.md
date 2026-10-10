# Launch KitchenTab (morning checklist, 30–45 minutes)

Everything below was rehearsed locally (`TARGET=local-vercel`) and every script was tested
against a stubbed `vercel` CLI. Nothing has been deployed yet. Run the steps in order from the
repo root in Git Bash (Windows) or a terminal (macOS/Linux). Every script accepts `DRY_RUN=1`,
which prints each command and runs none.

| # | Step | Time |
|---|---|---|
| 1 | Install the CLI: `npm i -g vercel` (Node 20+) | 2 min |
| 2 | `vercel login` (browser opens; sign in with the account that will own the project) | 2 min |
| 3 | `make deploy-setup` | 5–10 min |
| 4 | Two dashboard switches (printed by step 3) | 3 min |
| 5 | `make deploy` | 5–8 min |
| 6 | `make launch-check TARGET=prod` and `make e2e-multi TARGET=prod` | 10 min |
| 7 | `make prod-clean-test-data ARGS=--yes` | 1 min |
| 8 | Gmail app password, then `make set-email` | 5 min |
| 9 | Sign in, create the house, share the invite link | 3 min |
| 10 | Install on iPhone and Android, allow notifications | 5 min |

## 1–2. CLI and login

```bash
npm i -g vercel
vercel login
```

## 3. `make deploy-setup`

Links this folder to a new project `kitchentab`, adds **Neon** Postgres from the Marketplace
(free plan; accept the prompts), creates the private **Blob** store `kitchentab-receipts`, and
generates and stores every environment variable for production and preview: session and token
secrets, `CRON_SECRET`, a VAPID key pair for Web Push, the 64+ character `TEST_INBOX_SECRET`,
and the provider settings (`PROFILE=vercel`, `QUEUE_PROVIDER=vercel`, `STORAGE_PROVIDER=vercel_blob`,
`PUSH_PROVIDER=webpush`, `EMAIL_PROVIDER=testinbox` until step 8, `SMS_PROVIDER=none`,
`EXTRACTOR_PROVIDER=local`). It asks once for the postal address printed in email footers
(`EMAIL_POSTAL_ADDRESS`; required by anti-spam law for notification emails; you can add it later
with `vercel env add`). Safe to re-run after any failure: existing values are kept and secrets
are never regenerated.

## 4. Dashboard switches

The CLI can't set these. In the Vercel dashboard for `kitchentab`:

- **Settings → Build and Deployment → Framework Preset → Services** (the project has a Next.js
  web service and a FastAPI service in `vercel.json`).
- **Queues → Enable** (notifications are delivered through the `outbox-drain` queue).

Then re-run `make deploy-setup`; it exits 0 when nothing is left to do.

## 5. `make deploy`

Runs lint and unit tests, applies database migrations to Neon's direct (unpooled) URL, deploys to
production, works out the production URL, sets `WEB_ORIGIN`, `PUBLIC_LINK_BASE` and the Google
redirect URI to it, and redeploys once if they changed. It prints the URL at the end.

## 6. Production checks

```bash
make launch-check TARGET=prod
make e2e-multi TARGET=prod
```

`launch-check` finds the production URL that `make deploy` saved (or pass `ARGS=--base-url=https://…`)
and pulls `TEST_INBOX_SECRET` with the Vercel CLI unless it is already in your environment. It signs
up throwaway users at `@launch-check.kitchentab.test`, reads their codes from the test inbox
(`/api/test/inbox` with `X-Test-Secret`) and prints PASS/FAIL per step with timings, in this order:
health and `vercel.json` routing; security headers and CSP (page and API, HSTS); manifest, service
worker and HTTPS; sign-up by email code; create house; two roommates join by the invite link; a
text PDF bill through the real upload path (Blob client upload, then attach) with items and total
checked; split; send (a roommate ticks herself on the open tab); a photo bill through the OCR path;
a payment; settle; a kitchen item; an ask answered with "add to split"; push (printed as a manual
check: Settings › Notifications › Send a test on your phone); a 19 MB 10-page PDF through Blob
while a 5 MB JSON body to the API gets 413; an SSE stream held across the 270 s rollover with no
missed or duplicate events (this step alone takes about 5 minutes; `SSE_SECONDS=30` for a quick
run). Any FAIL exits non-zero. A cold Neon start makes the first request slow; the checks allow
for it.

`make e2e-multi TARGET=prod` runs the multi-roommate browser journeys against production and needs
`KT_PROD_URL=https://<project>.vercel.app` and `TEST_INBOX_SECRET` in the environment.

The same checks run locally against the production builds: `make local-vercel-up`, then
`make launch-check TARGET=local-vercel` (also runs the `@launch` browser checks: install criteria,
service worker, browser OCR) and `make e2e-multi TARGET=local-vercel`; `make local-vercel-down`
stops it (ADR-0028).

## 7. Remove the check data

```bash
make prod-clean-test-data          # preview: counts only, nothing deleted
make prod-clean-test-data ARGS=--yes
```

Only houses created by `@launch-check.kitchentab.test` users in which nobody else appears are
deleted, plus those users. Real data can't match.

## 8. Real sign-in emails (Gmail)

1. Turn on 2-Step Verification for the Gmail account, then create an app password at
   https://myaccount.google.com/apppasswords (name it "KitchenTab"). Copy the 16 letters.
2. `make set-email`: asks for the address and app password (hidden), stores the SMTP settings,
   switches `EMAIL_PROVIDER=smtp`, removes the test inbox secret, redeploys, confirms
   `/api/test/inbox` is gone, sends you a real sign-in code and asks you to type it in.

## 9. Your house

Open the production URL, sign in with your email code, create the house, add your roommates' names,
and share the invite link (WhatsApp/iMessage show a preview card).

## 10. Install and notifications

- **iPhone (iOS 16.4+):** open the URL in Safari → Share → **Add to Home Screen** → open KitchenTab
  from the Home Screen, sign in once more (the Home Screen app has its own cookies), then tap
  **Turn on notifications** when asked and allow.
- **Android:** open the URL in Chrome → **Install** (or ⋮ → Add to Home screen) → allow
  notifications when asked.
- Settings › Notifications › **Send a test** should buzz every device within a few seconds.

## When something fails

| Message | Meaning | Fix |
|---|---|---|
| `Not logged in to Vercel` | `vercel whoami` failed | `vercel login`, re-run |
| `exit 3: dashboard step needed` | Services preset or Queues not on yet | Step 4, re-run `make deploy-setup` |
| `Linking failed` | Name taken or wrong scope | `vercel link` by hand and pick the scope, re-run |
| `DATABASE_URL missing` | Neon integration not finished | Finish it in the dashboard (Storage → Neon), re-run setup |
| `NEON_DIRECT_URL needed` | Neon marked the unpooled URL Sensitive, so `env pull` can't read it | Copy it from Neon's dashboard: `NEON_DIRECT_URL=... make deploy` |
| `Migration failed` | Alembic error against Neon | Nothing was deployed; read the error, fix, `make deploy` again |
| `Checks failed` (lint/test) | Code isn't green | Fix, commit, `make deploy` |
| Boot error naming a setting | A required env var is missing or a placeholder | `vercel env add NAME production`, then `make redeploy` |
| No push on your phone after "Send a test" | Queues not enabled, or VAPID keys missing | Step 4; `vercel env ls` |
| `launch-check`: `the test inbox is off` | `make set-email` already ran | Run launch-check before step 8 |
| `set-email`: code never arrives | App password wrong or 2-Step off | Re-create the app password, `make set-email` again |

## Day to day

- **Ship a change:** commit, then `make deploy` (runs checks and migrations first).
- **Change a setting:** `vercel env add NAME production` (or `vercel env rm`), then `make redeploy`.
- **Roll back:** `make rollback` lists recent production deployments; pick one (or
  `ARGS="--to 2"`). Database migrations are additive and stay.
- **Back up:** Neon keeps point-in-time restore (free plan: 24 h). The daily cron also writes a
  logical export to the Blob store under `backups/`.
- **Restore:** create a Neon branch at a point in time and point `DATABASE_URL` at it, or load an
  export into a fresh database with `uv run --project apps/api python -m app.tools.restore_drill`
  as the template (`make restore-drill` proves the export round-trips locally).
- **Logs:** Vercel dashboard → Logs (filter by `api` service). Secrets, codes, tokens and full
  emails are never logged.

## Hobby limits to watch

| Limit | KitchenTab's use |
|---|---|
| Function duration 300 s | Realtime streams close at 270 s and reconnect without losing events |
| Request/response body 4.5 MB | Receipts upload straight to Blob (up to 20 MB), never through a function |
| Cron: once a day | One daily job (expire asks, retention, use-soon round-ups, digest, backup); timed notifications use delayed queue messages |
| Neon free: 0.5 GB storage, autosuspend | Cold start adds ~1 s to the first request after idle |
| Blob free tier | Receipt images are deleted 2 years after a tab settles |
| Function invocations / bandwidth | A household is far below the Hobby allowance |

## Launch log (2026-10-10)

Live at **https://kitchentab.vercel.app** (team `ed-i-th`, project `kitchentab`), sign-in codes from
a dedicated KitchenTab Gmail. What differed from the plan above, all fixed in the scripts:

- Vercel CLI 54: `blob create-store` replaces `blob store add`; Production/Preview variables are
  sensitive by default (`env pull` reads them back empty), so plain settings go in with
  `--no-sensitive`; Preview variables need a Git branch, so without Git set
  `KT_VERCEL_ENVIRONMENTS=production` (only production is deployed).
- `vercel deploy` uploads the folder, not Git: `.vercelignore` keeps it under the 100 MB limit.
- Vercel Services can't deploy Edge output: `middleware.ts` runs on the Node.js runtime.
- Neon was added from the dashboard (Storage → Neon) so its terms are accepted there.
- Run the scripts from a normal Windows terminal: apps that sandbox AppData (such as the Claude
  desktop app) don't see a globally installed `vercel` or its login. `scripts\set-email.cmd` is
  the double-click version of `make set-email`; `--provider other --smtp-host …` works with any
  STARTTLS relay if Gmail isn't an option.
- `make deploy`'s own lint+test gate was run separately (make lint, web unit 229/229, API suite
  538 passed, slow load/race/chaos groups excluded) before deploying.
