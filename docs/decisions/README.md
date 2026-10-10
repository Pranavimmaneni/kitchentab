# Architecture decision records

One file per non-obvious decision. Status is Accepted unless noted. Newer ADRs supersede older
ones where they say so.

| ADR | Decision |
| --- | --- |
| 0001 | PRD self-contradictions resolved (write kinds, pairwise netting, rounding, numbering) |
| 0002 | Reconciliation override = printed total + signed adjustment fee |
| 0003 | Member-or-guest references and schema gap fills |
| 0004 | Milestone-aware traceability and edge-case IDs |
| 0005 | Contrast fixes: `--control-edge`, violet #6A4CFF |
| 0006 | Same-origin deployment (supersedes the api.<domain> plan) |
| 0007 | CSP: hash for the static theme script, nonces only where needed |
| 0008 | MinIO image source for local dev |
| 0009 | joserfc for the mock OIDC issuer; no python-multipart |
| 0010 | Receipt extraction runs locally with no API key (C1) |
| 0011 | Deploy on Vercel Services, Hobby plan (C2) |
| 0012 | Realtime from Postgres `house_events`, not Redis (C2) |
| 0013 | Outbox drained through Vercel Queues; daily cron only (C2) |
| 0014 | Rate limits, idempotency and test inbox in Postgres (C2) |
| 0015 | Receipt storage: Vercel Blob client uploads, MinIO/fake locally (C2) |
| 0016 | Day-one sign-in: email codes via Gmail SMTP, test inbox until then (C3) |
| 0017 | `PROFILE=vercel` and `TARGET=local-vercel` rehearsal |
| 0018 | Derived link tokens (members can see links again without storing them) |
| 0019 | Web token adjustments (warn contrast, extra tokens, 44 px controls, Firefox path) |
| 0020 | Receipt fixtures: 30 deterministic synthetic receipts with truth files; Pillow for tests only |
| 0021 | Notification pipeline: dedupe keys, inbox rows, coalescing, quiet hours, cap, email rules, cron |
| 0022 | Morning deploy scripts: idempotency via `vercel env ls`, secrets on stdin and redacted, `@launch-check.kitchentab.test` test-data tag |
| 0023 | Concurrent ticks compute shares under the house lock; set-based owner sync (found by SYNC-24) |
| 0024 | Vercel Blob via the official SDKs; `/api/blob-view` signed views; stored scan offers (SYNC-15) |
| 0025 | Security suite: SEC-01 route x role matrix, audits through `scripts/audit_gate.py` (pip-audit added, waivers only in PROGRESS), bundle scan, ZAP gate |
| 0026 | Receipt capture in the browser: self-hosted tesseract.js/pdfjs under /ocr/ (eng model committed), canvas re-encode strips EXIF, on-device refusals, resumable in-memory scan job, integer `X-Client-Version` |
| 0027 | Service worker (Serwist): network-first 3 s reads marked stale, never-cache list, per-dist-dir worker name, update toast / 426 prompt, install and push moments, test approach |
| 0028 | `TARGET=local-vercel` rehearsal: Caddy edge with vercel.json routing + limits, PgBouncer + own Postgres, queue devserver driven by `poll_and_handle`, APP_ENV=test with a staging preflight; `make launch-check` |
| 0029 | Simulation findings: GET views read one REPEATABLE READ snapshot (SYNC-35); push device bookkeeping outside the drain batch (PUSH-19); harness runs single-process instances, not `uvicorn --workers` |
