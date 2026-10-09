# ADR-0014: Rate limits, idempotency and test inbox in Postgres (BUILD_ALL C2)

`idempotency_keys` already lives in Postgres. Rate limits use a `rate_limit_hits` table with a
fixed-window counter (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING count`) keyed by
`(bucket, key, window_start)`, purged daily. The email test inbox is the `test_inbox` table. Redis
is optional in every profile; with `PROFILE=local` it serves only the arq queue, the test clock
mirror and the early-wake publish.
