# ADR-0022: Morning deploy scripts (BUILD_ALL §6 step 3)

`make deploy-setup deploy redeploy rollback set-email prod-clean-test-data` run Python 3.12
stdlib scripts in `scripts/deploy/` (through `uv run --project apps/api`, so the API environment
is available: `prod-clean-test-data` uses asyncpg, `set-email --local` uses uvicorn and
`cryptography`). Every script echoes each command before running it, supports `--dry-run`
(`make <target> DRY_RUN=1`: prints every command, runs none, resolves `vercel` only to print its
path), and turns CLI failures into one message plus what to do, with a non-zero exit (1 failure,
2 usage, 3 "dashboard step needed, then re-run").

**Idempotency.** State is read from Vercel, never from a local cache: `.vercel/project.json` for
the link, `vercel env ls <env>` for every variable (Neon is "done" when `DATABASE_URL` exists, Blob
when `BLOB_READ_WRITE_TOKEN` exists). Existing variables are skipped, so a re-run after any
failure only adds what is missing. A variable `env ls` doesn't show but `env add` says exists
stops the run with a clear message instead of guessing. Ordering makes partial runs safe:
`TEST_INBOX_SECRET` is added before `EMAIL_PROVIDER` (and is not re-added once `EMAIL_PROVIDER`
exists, because `make set-email` removes it on purpose); the VAPID private key is added before the
public key, and a lone private key is replaced by a new pair (nothing can have subscribed to an
incomplete pair). `make deploy` compares WEB_ORIGIN, PUBLIC_LINK_BASE, GOOGLE_REDIRECT_URI (and
VAPID_SUBJECT when unset) with the pulled production values and redeploys only if one changed.
`APP_ENV` is `prod` for production and `staging` for preview (both are deployed modes in
`settings.py`); rate-limit variables are left at their defaults.

**Secrets.** Generated with `secrets.token_urlsafe` (SESSION_SECRET and TEST_INBOX_SECRET 64
bytes = 86 chars; TOKEN_HASH_PEPPER and CRON_SECRET 48 bytes = 64 chars), separately per
environment; VAPID is a P-256 pair computed with integer arithmetic (raw base64url, the format
pywebpush and browsers use; tests check it against `cryptography` and `py_vapid`). Values go to
`vercel env add` on stdin, never argv; they are wrapped in a `Secret` whose `str()` is
`<secret: N chars>`, and all output passes through a redactor that also knows every non-public
value pulled with `vercel env pull`. The pull goes to a temp folder outside the repo that is
deleted as soon as it is read. Emails are printed masked (`p***@gmail.com`). The Gmail app password
is read with `getpass` (or one stdin line when piped) and must look like an app password (16
letters) before anything is changed.

**Test data tag.** Production checks (`launch-check TARGET=prod`, `e2e-multi TARGET=prod`) sign
up only with addresses at `@launch-check.kitchentab.test` (a reserved `.test` domain nobody can
own; `deploylib.TEST_EMAIL_DOMAIN`). `prod-clean-test-data` deletes: houses created by such users
in which no other user appears anywhere (member, guest, bill author, uploader, allocation author,
payment recorder or confirmer, activity actor, seen marker, notification); everything inside
them; the tagged users nobody else still references (their sessions, identities, prefs and
subscriptions cascade); test-inbox mail to the domain. Without `--yes` the deletes run in a
transaction that is rolled back, so the preview counts are exactly what `--yes` deletes. With
`--yes` it commits only if every table shrank by exactly the planned rows and the number of
untagged users is unchanged. A test against the migrated schema fails if a new foreign key to
`users` or to house data appears that the plan doesn't know. Receipt blobs are not deleted.

**Other choices.** `make redeploy` is `vercel redeploy <the deployment the domain serves>` (found
with `vercel inspect <origin>`), for env changes; code changes go through `make deploy`. `make
rollback` lists ready production deployments and runs `vercel promote <url> --yes`; migrations
are additive and are not rolled back. `--skip-checks` (deploy) is accepted only with `--dry-run`.
`set-email --local` runs the real API in-process with `EMAIL_PROVIDER=smtp` against a small
STARTTLS+AUTH relay (throwaway self-signed cert for `localhost`, trusted via `SSL_CERT_FILE`) that
forwards to Mailpit, so the Gmail code path is exercised end to end. Queues and the Services
framework preset have no CLI command; `deploy-setup` prints the dashboard steps.

Tests (`scripts/tests/test_deploy_*.py`, VERCEL-10) run every script against fake `vercel`, `uv`
and `make` executables (Python plus `.cmd`/sh shims) on a PATH that contains only the shim folder
and the OS folder, record each call in JSON, and cover dry-run, happy path, re-runs, each failure
switch and secret leakage.
