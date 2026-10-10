# ADR-0027: Service worker, install flows and push on the web

Status: accepted · Milestone 9 (web) · PRD §20, §21.6

1. **Serwist, registered by the app.** `app/sw.ts` is built by `@serwist/next` (off in `next dev`).
   A build with `KT_DIST_DIR` writes `/sw-<dir>.js` instead of `/sw.js` (exposed as
   `NEXT_PUBLIC_KT_SW_URL`) so two local suites building side by side never serve each other's
   precache list. `skipWaiting: false`, `clientsClaim: true`. Precache: Next's static output, the
   offline page `/~offline`, the manifest and three icons; nothing from `public/` (the OCR assets
   are cached on first use instead).
2. **Runtime rules** (`lib/pwa/sw-rules.ts`, unit-tested): app navigations and `GET /api/v1/*`
   reads are network-first with a 3 s timeout (`lib/pwa/network-first.ts`); a slow or missing
   network serves the saved copy with `x-kt-stale: 1`, which the API client turns into the
   "SLOW CONNECTION · SHOWING SAVED TABS" banner (offline keeps its own banner). Never cached:
   auth, every non-GET, the event stream and its `?after=` catch-up, receipt URLs and images,
   uploads, push, exports, test/cron routes, link pages with tokens, anything cross-origin. The
   API's default `Cache-Control: no-store` is meant for shared HTTP caches, so the worker ignores it
   for read models; routes answering `private` opt out. Sign-in, sign-out and account deletion
   empty the saved screens and reads.
3. **Updates.** "New version ready · Refresh" shows only while no sheet/dialog is open, no field
   is focused and no write is in flight; nothing reloads by itself. A 426 STALE_CLIENT shows a
   blocking refresh prompt whatever is open.
4. **Moments** (`lib/pwa/moments.ts`): first bill sent (the send write or the share screen),
   first ask (sent, or one in the inbox), 2nd visit (a new browser session), first standalone
   launch, Settings. Push first, install card next time; the card shows at most twice per device,
   then only in Settings › Notifications (where "Install app" lives, since Settings only links
   there). Prompt memory is per device in localStorage.
5. **Analytics sink:** no provider is wired yet (PostHog or Plausible is a launch decision), so
   `pwa_installed` and install events go to `window.__ktEvents` / a `kt:analytics` event.
6. **Tests.** Playwright blocks service workers by default (`serviceWorkers: "block"`, so
   `page.route` keeps seeing every request); PWA, push, offline specs opt in. Push specs use the
   full Chromium build (`channel: "chromium"`): the headless shell reports
   `Notification.permission` as `denied` whatever is granted. Headless Chromium can't reach a
   real push service, so `PushManager` is stubbed in the page and the worker's `push`,
   `notificationclick` and `pushsubscriptionchange` handlers are driven with real event objects
   dispatched inside the worker; `clients.openWindow` needs a user gesture a synthetic click
   doesn't carry, so that call is recorded instead of performed.
7. **Not verified on real devices yet** (DEVICE_QA): iOS Home Screen cookie sharing (AUTH-13 shows
   "Welcome back. Sign in once more on this app" on any standalone launch until this device has
   been signed in there), iOS 16.4+ push from the Home Screen app, Android install sheet with the
   two screenshots.
