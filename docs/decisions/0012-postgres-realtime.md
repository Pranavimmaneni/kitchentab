# ADR-0012: Realtime from Postgres (BUILD_ALL C2)

Production has no Redis. `GET /api/v1/houses/{id}/events` streams SSE by polling `house_events`
where `seq > last` every 750 ms on the `(house_id, seq)` primary key, sends a heartbeat comment
every 20 s and closes cleanly at 270 s; clients reconnect with `Last-Event-ID` (PRD §21.5).
`house_write` keeps its post-commit publish hook: with `PROFILE=local` it publishes to Redis to
wake streams early; with `PROFILE=vercel` it is a no-op and polling alone delivers within 2 s.
Replay and gap fill (`?after=`) are the same query in both profiles.
