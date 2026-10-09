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
