# ADR-0017: PROFILE and TARGET

`PROFILE=local` (default): compose stack, Redis early-wake, arq worker, MinIO, Mailpit.
`PROFILE=vercel`: the same code with production adapters: Postgres-only realtime, Vercel Queues SDK
against its embedded/devserver, fake Blob service, `EMAIL_PROVIDER=testinbox`, no Redis.
`make test-sync PROFILE=vercel` and `make e2e-multi PROFILE=vercel` exercise these paths.
`TARGET=local-vercel` runs the production build (`vercel build` + `vercel dev -L` when available,
otherwise the same entrypoints and `vercel.json` rewrites through a local proxy) over HTTPS with
PgBouncer in front of Postgres (pooled + direct URLs) and Vercel limits enforced (4.5 MB bodies,
300 s duration). `TARGET=prod` is used only by Pranav in the morning.
