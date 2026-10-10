# ADR-0029: What the household simulation found (consistent reads, drain locks, instances)

Status: accepted · 2026-10-09 · BUILD_ALL §4.3 · TEST_PLAN SYNC-35, PUSH-19

## Context

`scripts/simulate_household.py` runs four roommates on six devices against several API instances
at once (manual, PDF and photo bills, ticks, price edits, payments with double taps and lost
responses, asks, used up, a roommate removed and re-added, tab sleeps, server restarts). Its first
runs passed every invariant check yet left 500s in the server logs. The simulation had not been
counting them: reads and drain calls weren't checked. It now fails on any 5xx outside a deliberate
restart. Three defects came out, plus one harness problem.

## Decisions

1. **Views read one snapshot (SYNC-35).** Handlers composed a view from several SELECTs under READ
   COMMITTED. A price edit committing between "read the bill" and "read its items" gave an old
   `total_cents` next to new prices. `compute_shares` refused to reconcile them, and
   `GET /houses/{id}/tabs` returned 500 (the bill view hid it behind its preview fallback, showing
   wrong numbers instead). GET view handlers now use `ctx.read_sessions`: read-only REPEATABLE READ,
   which Postgres never fails with serialization errors, and which works through PgBouncer because
   the level travels with `BEGIN`. `bill_math.load` re-reads a bill that was loaded in another
   session, so its children and the bill come from the same snapshot (`money` loads the bill for
   the permission check first). Writes stay READ COMMITTED with explicit row locks (ADR-0023):
   REPEATABLE READ writers would fail with serialization errors under exactly this concurrency.
   GET handlers that write keep the write sessions: the event stream, the daily cron (Vercel Cron
   sends GET), the Google callback and the auth dependency's sliding expiry.

2. **Device bookkeeping leaves the drain's batch transaction (PUSH-19).** `last_success_at`,
   `failure_count`, stale marks and 410 deletes were written inside the batch, so each device
   row stayed locked until the whole batch committed. Two drains (queue consumers on two
   instances) delivering to the same person deadlocked. The victim's error handler then read
   an expired ORM attribute (`MissingGreenlet`), the drain failed with a 500, and its batch rolled
   back. Those pushes had already gone out, so the next drain sent them again: the simulation saw
   the same push reach a phone twice. Now each notification's device outcomes are written in their
   own short transaction, rows locked in id order (no lock cycles), and the error handler marks the
   failed row by id with an explicit UPDATE. Delivery stays at-least-once (a crash between the push
   and the commit can still repeat one notification); the service worker's `tag` keeps that to one
   visible notification.

3. **Client-disconnect noise is accepted.** When a client drops a request mid-query, SQLAlchemy's
   pool logs "Exception terminating connection" at ERROR. The connection is discarded and nothing
   is half-written (the transaction never committed). This is library behavior; we don't wrap it.

4. **Harness: several single-process instances, not `uvicorn --workers`.** On Windows, uvicorn's
   multi-worker mode shares one listening socket, and some new connections went unanswered for over
   40 s while every worker reported started. Separately, stopping the supervisor left its workers
   alive, each holding a database pool: 120 orphans exhausted Postgres' 100 connections. The
   multi-process harness (`tests/harness/multiserver.py`, SYNC-24) and the simulation now start N
   single-process instances on N ports in their own process group, wait until each one logs
   startup, and stop the whole tree. Clients are spread over the instances, so a write and the
   stream that must carry it usually run in different processes. This is closer to Vercel's many
   short-lived instances anyway.

## Consequences

- One extra SELECT on read paths that load a bill twice; none on write paths.
- PUSH-19 and SYNC-35 are new TEST_PLAN rows with deterministic regression tests in
  `tests/test_races_found_by_simulation.py` (each fails with its fix reverted).
- `scripts/simulate_household.py` reports read and drain 5xx, stream reconnect reasons and per-device
  duplicate pushes, and holds a lock so that no restart or half-done removal straddles a checkpoint.
