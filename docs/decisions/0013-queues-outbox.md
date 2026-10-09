# ADR-0013: Outbox drained through Vercel Queues (BUILD_ALL C2)

The write transaction inserts `outbox` rows (unchanged). After commit the API sends a Vercel
Queues message `outbox-drain {house_id, seq}` (Python SDK `vercel-queue`); a push subscriber
declared under `[[tool.vercel.subscribers]]` drains pending rows with
`SELECT ... FOR UPDATE SKIP LOCKED`, fans out with preferences, quiet hours and dedupe keys, and
writes `notifications`. Quiet-hours and reminder deliveries are delayed messages (`delay=`). A
sweeper endpoint, called by the single daily Hobby cron and after each drain, picks up leftovers.
Locally: the arq worker (`PROFILE=local`) or the SDK's embedded queue / `vercel.queue.devserver`
(`PROFILE=vercel`). The daily cron also runs use-soon scans, retention purges and the daily
logical export; everything time-sensitive uses delayed messages because Hobby cron runs at most
daily with plus or minus 59 min precision. Source: vercel.com/docs/queues/python-sdk.
