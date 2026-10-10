# ADR-0021: Notification pipeline details (PRD §20.2, M9)

Status: accepted · Milestone 9 · Related: ADR-0013 (queues), ADR-0016 (test inbox)

The PRD fixes the pipeline (outbox → fan-out → `notifications` rows → delivery). These are the
choices it left open, all in `apps/api/app/services/notify.py`.

1. **Dedupe key** is `house_id:seq:type:user_id:channel` (the PRD's `event_id:user_id:channel`
   with the type added, because one event may notify two types). Inserts use
   `ON CONFLICT DO NOTHING`, so a retried drain never duplicates (PUSH-13).
2. **In-app rows are the inbox.** Every recipient gets one, status `sent`, whatever their
   preferences ("turning off a type never hides it in the app"). Push and email rows follow the
   type × channel matrix. Defaults are the N2 mockup's; `joined` (admins only, not in N2) is push
   on and email off.
3. **Coalescing (PUSH-11)** opens a 10-minute window at the event's commit time, not at fan-out
   time, so a late drain cannot stretch it. Later share changes for the same bill fold into the
   waiting row (net delta, latest share). The cause text ("Meera joined the coffee beans.") is
   shown only when the row covers one change. A net change under 50¢ is suppressed at send time.
4. **Quiet hours (PUSH-10)** hold push and email rows until the window's end in the user's time
   zone. At delivery, held rows of one type for one user and channel become one grouped
   notification ("2 asks for your food"). After each drain the next due time is scheduled as a
   delayed queue message (Vercel Queues `delay=`); the arq worker simply polls every 10 s.
5. **Daily cap (PUSH-12)** counts pushes sent since local midnight; overflow is `suppressed`
   with `daily_cap` and stays in the inbox. The "It works!" test push is outside the cap and
   ignores quiet hours, because the user just asked for it.
6. **`POST /push/test`** goes through `notifications` like everything else (rule 6: no push from
   a request handler). It needed a `test` notification type: migration 0005 widens the CHECK.
7. **Subscriptions** are naturally idempotent on the endpoint (a repeated POST updates the row);
   `replaces_endpoint` handles `pushsubscriptionchange` (PUSH-04). 404/410 delete; five other
   failures mark the device stale (PUSH-05). Device labels come from the user agent.
8. **Badge (PUSH-16)** is the count of unread in-app rows of types `asks` and `share`. Screens mark
   rows read with `POST /me/inbox/read` by ids, types or URL.
9. **Dev VAPID keys.** With `PUSH_PROVIDER=fake` and no keys configured, the key pair is derived
   from `SESSION_SECRET` with HMAC, so the API and workers agree and no key sits in the repo.
   Deployed settings validation already requires real keys with `PUSH_PROVIDER=webpush`.
10. **Email (EMAIL-01..03)** goes only to verified addresses, with `List-Unsubscribe` and RFC 8058
    `List-Unsubscribe-Post`, a footer saying why it was sent and `EMAIL_POSTAL_ADDRESS`
    (set before launch), and an HTML part that follows the reader's light or dark mode. The
    unsubscribe link is signed with `TOKEN_HASH_PEPPER` over user and type. Its GET shows a confirm
    page and only POST changes anything, because link scanners follow GETs. The path is
    CSRF-exempt because the signature authenticates it. The weekly digest is email-only, Sunday
    10:00 house time, scheduled by the daily cron when that time is under 36 h away.
11. **Cron and sweeper**: `GET /api/v1/cron/daily` and `POST /api/v1/cron/drain` take
    `Authorization: Bearer CRON_SECRET` (constant-time compare). Daily: expire asks, retention purge,
    use-soon round-ups for the next 9:00 house time (KITCH-15, once per item state), the digest,
    then a drain. Every step is idempotent (VERCEL-09).
12. **Tests** run `QUEUE_PROVIDER=inline` and drive `POST /__test/drain` with the controllable
    clock. VERCEL-04 runs the real `vercel-queue` SDK against its embedded queue service, and the
    `app/queues/outbox.py` subscriber does the draining.
