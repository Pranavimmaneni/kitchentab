# ADR-0011: Vercel Services on the Hobby plan (BUILD_ALL C2)

One project, one domain. `vercel.json` declares `services.web` (root `apps/web`, framework
`nextjs`) and `services.api` (root `apps/api`, framework `fastapi`, entrypoint
`app.vercel_entry:app`), with top-level rewrites `/api/(.*)` to api and `/(.*)` to web (rewrites
are evaluated in order; requests keep their full path). Hobby limits are enforced in code and in
the local-vercel rehearsal: 4.5 MB request/response bodies, 300 s max duration (SSE closes at
270 s), 2 GB memory, Python bundle under 500 MB (no OCR or image libraries on the server).
Postgres is Neon from the Marketplace: pooled URL for requests, direct URL for Alembic during
`make deploy`, never from a request. Services is a beta: the project framework must be set to
Services; `make deploy-setup` documents this and the stubbed-CLI tests cover the config.
Sources: vercel.com/kb/guide/vercel-services, vercel.com/docs/services/routing.
