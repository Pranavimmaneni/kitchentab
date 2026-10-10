/**
 * Service-worker event handlers (PRD §20.2), written against small interfaces so they are
 * unit-tested; `app/sw.ts` passes the real `self.registration` / `self.clients`.
 *
 *   push                    show `{title, body, url, tag, badge}` (collapse by tag), set the app
 *                           badge, tell open windows so they catch up (PRD §21.5)
 *   notificationclick       focus an open KitchenTab window and navigate it, else open one (PUSH-15)
 *   pushsubscriptionchange  subscribe again and replace the old endpoint on the server (PUSH-04)
 */

export type PushPayload = {
  title: string;
  body: string;
  url: string;
  tag: string | null;
  badge: number | null;
  event_id: string | null;
};

export const BADGE_ICON = "/icons/badge-96.png";
export const NOTIFICATION_ICON = "/icons/icon-192.png";

/** Only paths on this origin: a payload can never send people elsewhere. */
export function safePath(value: unknown): string {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  ) {
    return "/";
  }
  return value;
}

export function parsePush(
  data: { json(): unknown; text(): string } | null | undefined,
): PushPayload {
  let raw: Record<string, unknown> = {};
  try {
    const parsed = data?.json();
    if (parsed && typeof parsed === "object") raw = parsed as Record<string, unknown>;
  } catch {
    const text = data?.text();
    if (text) raw = { body: text };
  }
  const str = (v: unknown) => (typeof v === "string" ? v : null);
  return {
    title: str(raw.title) || "KitchenTab",
    body: str(raw.body) ?? "",
    url: safePath(raw.url),
    tag: str(raw.tag),
    badge:
      typeof raw.badge === "number" && Number.isFinite(raw.badge)
        ? Math.max(0, Math.floor(raw.badge))
        : null,
    event_id: str(raw.event_id),
  };
}

export function notificationOptions(p: PushPayload) {
  return {
    body: p.body,
    icon: NOTIFICATION_ICON,
    badge: BADGE_ICON,
    data: { url: p.url, event_id: p.event_id },
    // A newer notification with the same tag replaces the older one (PRD §20.2 collapse tags).
    ...(p.tag ? { tag: p.tag, renotify: true } : {}),
  };
}

export type WindowClientLike = {
  url: string;
  type?: string;
  focused?: boolean;
  visibilityState?: string;
  focus(): Promise<unknown>;
  navigate?(url: string): Promise<unknown>;
  postMessage(message: unknown): void;
};

export type PushDeps = {
  showNotification(title: string, options: ReturnType<typeof notificationOptions>): Promise<void>;
  setAppBadge?(count: number): Promise<void>;
  clearAppBadge?(): Promise<void>;
  windows(): Promise<WindowClientLike[]>;
};

export async function handlePush(payload: PushPayload, deps: PushDeps): Promise<void> {
  const tasks: Promise<unknown>[] = [
    deps.showNotification(payload.title, notificationOptions(payload)),
  ];
  if (payload.badge !== null) {
    if (payload.badge > 0 && deps.setAppBadge)
      tasks.push(deps.setAppBadge(payload.badge).catch(() => undefined));
    if (payload.badge === 0 && deps.clearAppBadge)
      tasks.push(deps.clearAppBadge().catch(() => undefined));
  }
  for (const client of await deps.windows())
    client.postMessage({ type: "kt:push", url: payload.url });
  await Promise.all(tasks);
}

export type ClickDeps = {
  origin: string;
  windows(): Promise<WindowClientLike[]>;
  openWindow(url: string): Promise<unknown>;
};

/** Focus a KitchenTab window and take it to `path`; with none open, open one there (PUSH-15). */
export async function handleNotificationClick(
  path: string,
  deps: ClickDeps,
): Promise<"focused" | "opened"> {
  const target = new URL(safePath(path), deps.origin).href;
  const ours = (await deps.windows()).filter((c) => {
    try {
      return new URL(c.url).origin === deps.origin;
    } catch {
      return false;
    }
  });
  const best =
    ours.find((c) => c.focused) ?? ours.find((c) => c.visibilityState === "visible") ?? ours[0];
  if (best) {
    await best.focus().catch(() => undefined);
    if (best.url !== target) {
      let navigated = false;
      if (best.navigate) {
        try {
          navigated = Boolean(await best.navigate(target));
        } catch {
          navigated = false;
        }
      }
      // Not controlled by this worker (or navigate refused): the app routes itself.
      if (!navigated) best.postMessage({ type: "kt:navigate", url: safePath(path) });
    }
    return "focused";
  }
  await deps.openWindow(target);
  return "opened";
}

export type SubscriptionLike = {
  endpoint: string;
  toJSON(): { endpoint?: string; keys?: Record<string, string> };
  options?: { applicationServerKey?: ArrayBuffer | null };
};

export type ResubscribeDeps = {
  origin: string;
  subscribe(applicationServerKey: ArrayBuffer | Uint8Array): Promise<SubscriptionLike>;
  fetch(input: string, init?: RequestInit): Promise<Response>;
  newId(): string;
};

export function base64UrlToBytes(value: string): Uint8Array {
  const padded =
    value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/**
 * `pushsubscriptionchange`: the push service rotated the subscription. Subscribe again (with the
 * same VAPID key) unless the browser already did, then register the new endpoint in place of the
 * old one (`replaces_endpoint`, ADR-0021 #7). The worker has no cookies API everywhere, so it gets
 * the CSRF token from `GET /auth/csrf` like the app does.
 */
export async function handleSubscriptionChange(
  change: { oldSubscription?: SubscriptionLike | null; newSubscription?: SubscriptionLike | null },
  deps: ResubscribeDeps,
): Promise<boolean> {
  let next = change.newSubscription ?? null;
  if (!next) {
    let key: ArrayBuffer | Uint8Array | null | undefined =
      change.oldSubscription?.options?.applicationServerKey;
    if (!key) {
      const res = await deps.fetch(`${deps.origin}/api/v1/push/config`, {
        credentials: "same-origin",
      });
      if (!res.ok) return false;
      key = base64UrlToBytes(((await res.json()) as { vapid_public_key: string }).vapid_public_key);
    }
    next = await deps.subscribe(key);
  }
  const json = next.toJSON();
  if (!json.keys?.p256dh || !json.keys.auth) return false;
  const csrfRes = await deps.fetch(`${deps.origin}/api/v1/auth/csrf`, {
    credentials: "same-origin",
  });
  if (!csrfRes.ok) return false;
  const { token } = (await csrfRes.json()) as { token: string };
  const res = await deps.fetch(`${deps.origin}/api/v1/push/subscriptions`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": token,
      "Idempotency-Key": deps.newId(),
      "X-Client-Mutation-Id": deps.newId(),
      "X-Client-Version": "1",
    },
    body: JSON.stringify({
      endpoint: next.endpoint,
      keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
      ...(change.oldSubscription?.endpoint && change.oldSubscription.endpoint !== next.endpoint
        ? { replaces_endpoint: change.oldSubscription.endpoint }
        : {}),
    }),
  });
  return res.ok;
}
