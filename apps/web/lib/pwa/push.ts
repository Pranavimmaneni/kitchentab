"use client";

/**
 * Web Push on this device (PRD §20.2): turning it on from a tap, and keeping the server's copy of
 * this device's subscription current on every start (PUSH-02, PUSH-04).
 */
import { api, isApiError } from "@/lib/api/client";
import { pushSupported } from "./device";
import { KEYS, getString, rememberDenied, setString } from "./local";
import { base64UrlToBytes } from "./sw-handlers";

export type Device = {
  id: string;
  device_label: string;
  endpoint_hash: string;
  created_at: string;
  last_success_at: string | null;
  stale: boolean;
};

export type EnableResult = "granted" | "denied" | "default" | "unsupported" | "error";

/** The worker's registration, if one controls (or will control) this page within `ms`. */
export async function swRegistration(ms = 8000): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  const existing = await navigator.serviceWorker.getRegistration().catch(() => undefined);
  if (existing?.active) return existing;
  return Promise.race([
    navigator.serviceWorker.ready.catch(() => null),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), ms)),
  ]);
}

/** First 16 hex chars of SHA-256, the way the API lists devices (`endpoint_hash`). */
export async function endpointHash(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 16);
}

function sameKey(a: ArrayBuffer | null | undefined, b: Uint8Array): boolean {
  if (!a) return true; // the browser doesn't say: assume it is ours
  const x = new Uint8Array(a);
  return x.length === b.length && x.every((v, i) => v === b[i]);
}

async function vapidKey(): Promise<Uint8Array> {
  const { vapid_public_key } = await api.get<{ vapid_public_key: string }>("/push/config");
  return base64UrlToBytes(vapid_public_key);
}

async function register(sub: PushSubscription, replaces: string | null): Promise<void> {
  const json = sub.toJSON();
  await api.post("/push/subscriptions", {
    endpoint: sub.endpoint,
    keys: { p256dh: json.keys?.p256dh, auth: json.keys?.auth },
    ...(replaces && replaces !== sub.endpoint ? { replaces_endpoint: replaces } : {}),
  });
  setString(KEYS.endpoint, sub.endpoint);
}

async function subscription(reg: ServiceWorkerRegistration, key: Uint8Array): Promise<PushSubscription> {
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options?.applicationServerKey, key)) {
    await sub.unsubscribe().catch(() => false);
    sub = null;
  }
  return sub ?? reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key as BufferSource });
}

/**
 * "Turn on notifications": only ever called from a tap (PUSH-02). Asks the browser, subscribes with
 * the VAPID key and registers the device. A denial is remembered: this device is never asked again.
 */
export async function enablePush(): Promise<EnableResult> {
  if (!pushSupported()) return "unsupported";
  let permission: NotificationPermission;
  try {
    permission = await Notification.requestPermission();
  } catch {
    return "error";
  }
  if (permission === "denied") {
    rememberDenied();
    return "denied";
  }
  if (permission !== "granted") return "default";
  try {
    const reg = await swRegistration();
    if (!reg) return "error";
    const sub = await subscription(reg, await vapidKey());
    await register(sub, getString(KEYS.endpoint));
    return "granted";
  } catch {
    return "error";
  }
}

export type SyncResult = "off" | "unchanged" | "registered" | "replaced";

/**
 * On every start (PRD §20.2, PUSH-04): if push is allowed here, make sure the server has this
 * browser's current subscription; a changed endpoint replaces the old one.
 */
export async function syncSubscription(): Promise<SyncResult> {
  if (!pushSupported() || Notification.permission !== "granted") return "off";
  const reg = await swRegistration();
  if (!reg) return "off";
  try {
    const sub = await subscription(reg, await vapidKey());
    const previous = getString(KEYS.endpoint);
    const devices = await api.get<Device[]>("/push/subscriptions");
    const hash = await endpointHash(sub.endpoint);
    if (previous === sub.endpoint && devices.some((d) => d.endpoint_hash === hash)) return "unchanged";
    await register(sub, previous);
    return previous && previous !== sub.endpoint ? "replaced" : "registered";
  } catch (error) {
    if (isApiError(error) && error.status === 401) return "off";
    return "off";
  }
}

/** This device's `endpoint_hash`, to mark "YOU'RE HERE" in the device list. */
export async function currentDeviceHash(): Promise<string | null> {
  if (!pushSupported()) return null;
  const reg = await swRegistration(2000);
  const sub = await reg?.pushManager.getSubscription().catch(() => null);
  return sub ? endpointHash(sub.endpoint) : null;
}
