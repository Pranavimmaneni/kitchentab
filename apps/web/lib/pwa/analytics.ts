/**
 * Product analytics events without PII (PRD §12 Observability). No analytics provider is wired up
 * yet (PostHog or Plausible, a launch decision), so events are kept on `window.__ktEvents` and
 * announced as `kt:analytics`; the provider adapter subscribes to that later.
 */
import { KEYS, getString, setString } from "./local";

export type AnalyticsEvent = { name: string; at: number };

declare global {
  interface Window {
    __ktEvents?: AnalyticsEvent[];
  }
}

export function track(name: string): void {
  if (typeof window === "undefined") return;
  const event = { name, at: Date.now() };
  (window.__ktEvents ??= []).push(event);
  window.dispatchEvent(new CustomEvent("kt:analytics", { detail: event }));
}

/** Once per device, ever (e.g. `pwa_installed`, PWA-07). */
export function trackOnce(name: string): boolean {
  const key = `${KEYS.tracked}${name}`;
  if (getString(key)) return false;
  setString(key, String(Date.now()));
  track(name);
  return true;
}
