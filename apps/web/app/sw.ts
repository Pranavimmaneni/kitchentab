/// <reference lib="webworker" />
/**
 * KitchenTab service worker (PRD §20.1–20.2), built by @serwist/next into /sw.js. The rules live in
 * lib/pwa/sw-rules.ts, the strategy in lib/pwa/network-first.ts and the push/click/subscription
 * handlers in lib/pwa/sw-handlers.ts, all unit-tested; this file only wires them up.
 *
 * Updates: a new worker installs and waits (`skipWaiting: false`). The app shows "New version
 * ready · Refresh" and posts `{type: "SKIP_WAITING"}` when tapped (Serwist handles the message),
 * then reloads on `controllerchange` (PWA-08). The first install claims open pages so offline
 * works from the first visit.
 */
import { Serwist, type PrecacheEntry, type SerwistGlobalConfig } from "serwist";
import { networkFirst } from "@/lib/pwa/network-first";
import {
  CACHE_NAMES,
  OFFLINE_URL,
  USER_CACHES,
  changesSignedInUser,
  isApiRead,
  isAppNavigation,
  isOcrAsset,
} from "@/lib/pwa/sw-rules";
import {
  handleNotificationClick,
  handlePush,
  handleSubscriptionChange,
  parsePush,
  safePath,
  type SubscriptionLike,
  type WindowClientLike,
} from "@/lib/pwa/sw-handlers";

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}

declare const self: ServiceWorkerGlobalScope;

const ORIGIN = self.location.origin;

async function windows(): Promise<WindowClientLike[]> {
  const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  return list as unknown as WindowClientLike[];
}

async function offlinePage(): Promise<Response | undefined> {
  return (await caches.match(OFFLINE_URL, { ignoreSearch: true })) ?? undefined;
}

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  skipWaiting: false,
  clientsClaim: true,
  navigationPreload: false,
  disableDevLogs: true,
  runtimeCaching: [
    {
      matcher: ({ request }) => isApiRead(request, ORIGIN),
      handler: async ({ request, event }) =>
        networkFirst({
          request,
          kind: "api",
          cache: await caches.open(CACHE_NAMES.api),
          fetch: (r) => fetch(r),
          waitUntil: (p) => (event as ExtendableEvent).waitUntil(p),
        }),
    },
    {
      matcher: ({ request }) => isAppNavigation(request, ORIGIN),
      handler: async ({ request, event }) =>
        networkFirst({
          request,
          kind: "page",
          cache: await caches.open(CACHE_NAMES.pages),
          fetch: (r) => fetch(r),
          waitUntil: (p) => (event as ExtendableEvent).waitUntil(p),
          fallback: offlinePage,
        }),
    },
    {
      matcher: ({ request }) => isOcrAsset(request, ORIGIN),
      handler: async ({ request }) => {
        const cache = await caches.open(CACHE_NAMES.ocr);
        const hit = await cache.match(request);
        if (hit) return hit;
        const res = await fetch(request);
        if (res.ok && res.status === 200) await cache.put(request, res.clone());
        return res;
      },
    },
  ],
});

// Sign in, sign out, delete account: the saved screens and reads belonged to whoever was signed
// in, so they go (a shared tablet must not show the last person's tabs offline).
self.addEventListener("fetch", (event) => {
  if (!changesSignedInUser(event.request, ORIGIN)) return;
  event.respondWith(
    fetch(event.request).finally(() => {
      event.waitUntil(Promise.all(USER_CACHES.map((name) => caches.delete(name))));
    }),
  );
});

self.addEventListener("message", (event) => {
  const data = event.data as { type?: string } | null;
  if (data?.type === "CLEAR_USER_CACHES") {
    event.waitUntil(Promise.all(USER_CACHES.map((name) => caches.delete(name))));
  }
});

self.addEventListener("push", (event) => {
  const payload = parsePush(event.data);
  const nav = self.navigator as Navigator & {
    setAppBadge?: (n: number) => Promise<void>;
    clearAppBadge?: () => Promise<void>;
  };
  event.waitUntil(
    handlePush(payload, {
      showNotification: (title, options) => self.registration.showNotification(title, options),
      setAppBadge: nav.setAppBadge ? (n) => nav.setAppBadge!(n) : undefined,
      clearAppBadge: nav.clearAppBadge ? () => nav.clearAppBadge!() : undefined,
      windows,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data as { url?: unknown } | null;
  event.waitUntil(
    handleNotificationClick(safePath(data?.url), {
      origin: ORIGIN,
      windows,
      openWindow: (url) => self.clients.openWindow(url),
    }),
  );
});

type SubscriptionChangeEvent = ExtendableEvent & {
  oldSubscription?: PushSubscription | null;
  newSubscription?: PushSubscription | null;
};

self.addEventListener("pushsubscriptionchange", (event: Event) => {
  const change = event as SubscriptionChangeEvent;
  change.waitUntil(
    handleSubscriptionChange(
      {
        oldSubscription: (change.oldSubscription ?? null) as SubscriptionLike | null,
        newSubscription: (change.newSubscription ?? null) as SubscriptionLike | null,
      },
      {
        origin: ORIGIN,
        subscribe: (key) =>
          self.registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: key as BufferSource,
          }) as unknown as Promise<SubscriptionLike>,
        fetch: (input, init) => fetch(input, init),
        newId: () => crypto.randomUUID(),
      },
    ).catch(() => false),
  );
});

serwist.addEventListeners();
