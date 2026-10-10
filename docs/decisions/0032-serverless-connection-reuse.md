# ADR-0032: The serverless API keeps a small connection pool

Status: proposed (needs the owner's merge) · 2026-10-10 · amends ADR-0028 · TEST_PLAN SYNC-24, VERCEL-11

## Context

`PROFILE=vercel` (production) opened a new database connection for every session: NullPool, no
prepared-statement cache, unique statement names (ADR-0028), because Neon's pooled URL is PgBouncer
in transaction mode and Vercel runs many short-lived instances. Measured on the owner's laptop
(Docker Postgres, 4 single-process API workers):

| | Local profile (pooled) | Vercel profile (NullPool) |
| --- | --- | --- |
| One person ticking alone | 52 ms | 174 ms |
| Bill read | 32 ms | 106 ms |
| Time holding the house lock per tick | ~15 ms | ~290 ms |
| 20 people flat out | 58 ticks/s | 7 ticks/s |
| 20 people at human pace, p95 | under 300 ms | 4 s |

A request uses several sessions, and each one paid connection setup plus asyncpg's per-connection
type lookups (array codecs for the batched event insert, among others). The ones inside the house
lock serialized the whole house: every tick waited for every other tick's fresh connection.

## Decision

- The API's engine keeps `DB_SERVERLESS_POOL_SIZE` connections per instance (default 10, overflow
  half that), `pool_pre_ping` (an instance frozen between invocations may wake with dead
  connections) and `pool_recycle=300`. The FastAPI app is built once per instance and its lifespan
  owns the engine, so one event loop owns the pool.
- Prepared statements stay uncached with unique names: a connection from the pool is still a
  PgBouncer client in transaction mode, and no prepared statement may outlive its transaction.
- `DB_SERVERLESS_POOL_SIZE=0` restores the previous behaviour, as an escape hatch in the Vercel
  environment variables (a redeploy applies it).
- The outbox queue subscriber (`app/queues/outbox.py`) keeps NullPool: a queue invocation may run
  in a fresh event loop, and it isn't latency-critical.

## Consequences

- Laptop, Vercel profile with the pool: one tick 54 ms (was 174), lock held ~14 ms median (was
  ~290), flat-out SYNC-24 above its 20 ticks/s floor. Human-pace p95 on Windows Docker was still
  ~0.7–1.1 s because every statement is prepared fresh (an extra round trip, slow on Docker Desktop's
  localhost); the Linux CI runner is the reference measurement.
- Each warm instance holds up to 15 client connections to Neon's pooler, which accepts thousands;
  Postgres itself still sees only the pooler's server connections.
- Needs a check on a real deployment before merge: a preview with the pooled URL, or watch the first
  production requests after merging (rollback: `make rollback` or `DB_SERVERLESS_POOL_SIZE=0`).
