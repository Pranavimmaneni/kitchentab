# ADR-0031: SYNC-24 measures latency at human pace and capacity flat out

Status: accepted (owner's decision, 2026-10-10) · PRD §21.7 · TEST_PLAN SYNC-24 · amends ADR-0023

## Context

SYNC-24 ran 20 people ticking one bill "as fast as possible" for 30 s and required p95 write
< 300 ms. Ticks are serialized on the house lock (ADR-0023) so each one computes shares from every
committed allocation; a tick holds it about 15 ms (local profile). With 20 clients that never pause,
the mean wait alone is about 20 × 15 ms = 300 ms (Little's law), so the target could not be met with
this design however fast the rest of the request is. Measured p95: 0.63 s on the owner's laptop,
1.0 s in CI (local profile), 4.0 s in CI (Vercel profile). The real client never taps like that: it
holds each tap 250 ms and sends cells that come due together as one batch (`lib/split/coalesce.ts`),
and people tap at human speed.

## Decision (owner, 2026-10-10: "Realistic load")

SYNC-24 is two runs of 20 people on one bill:

1. **Flat out**: no pause between taps. No lost updates, Σ shares = total after every commit,
   nobody starved, every device converges, invariants hold, and the house absorbs at least
   **20 ticks/s** (what 20 people at human pace ask for).
2. **Human pace**: each person taps every 0.5–1.5 s and the client holds the tap 250 ms. The same
   correctness checks and **p95 write < 300 ms**.

## Consequences

- The latency promise matches how the app is used; the correctness checks still run under
  maximum contention.
- The Vercel profile (no connection pool, ADR-0028) failed both runs locally: 7 ticks/s flat out,
  p95 4 s at human pace. That is a production finding, addressed separately (connection reuse in
  the serverless profile).
