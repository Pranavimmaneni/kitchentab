/**
 * What this device can do (PRD §20.1–20.2): installed or in a tab, iPhone or not, push or not.
 * Everything reads browser globals lazily and is safe during server rendering.
 */

type NavigatorExtras = Navigator & {
  standalone?: boolean;
  setAppBadge?: (count?: number) => Promise<void>;
  clearAppBadge?: () => Promise<void>;
};

function nav(): NavigatorExtras | null {
  return typeof navigator === "undefined" ? null : (navigator as NavigatorExtras);
}

/** Opened from the home screen (`display-mode: standalone`, or `navigator.standalone` on iOS). */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (window.matchMedia?.("(display-mode: standalone)").matches) return true;
  } catch {
    // matchMedia missing (tests)
  }
  return nav()?.standalone === true;
}

/** iPhone, iPad (iPadOS reports a Mac with touch) or iPod. */
export function isIos(
  ua = nav()?.userAgent ?? "",
  touchPoints = nav()?.maxTouchPoints ?? 0,
): boolean {
  return /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && touchPoints > 1);
}

/** Push needs a service worker, PushManager and Notification (an iPhone tab has none of them). */
export function pushSupported(): boolean {
  if (typeof window === "undefined") return false;
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export function notificationPermission(): NotificationPermission | "unsupported" {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission;
}

/** "iPhone", "Android phone", "Mac"… for "For this iPhone, from the Home Screen app." */
export function deviceName(
  ua = nav()?.userAgent ?? "",
  touchPoints = nav()?.maxTouchPoints ?? 0,
): string {
  if (/iPhone|iPod/i.test(ua)) return "iPhone";
  if (/iPad/i.test(ua) || (/Macintosh/i.test(ua) && touchPoints > 1)) return "iPad";
  if (/Android/i.test(ua)) return /Mobile/i.test(ua) ? "Android phone" : "Android tablet";
  if (/Macintosh|Mac OS X/i.test(ua)) return "Mac";
  if (/Windows/i.test(ua)) return "Windows PC";
  if (/CrOS/i.test(ua)) return "Chromebook";
  if (/Linux/i.test(ua)) return "computer";
  return "browser";
}

/** Desktop Safari installs with File → Add to Dock (PRD §20.1 table). */
export function isDesktopSafari(
  ua = nav()?.userAgent ?? "",
  touchPoints = nav()?.maxTouchPoints ?? 0,
): boolean {
  return (
    /Macintosh/i.test(ua) &&
    touchPoints <= 1 &&
    /Safari/i.test(ua) &&
    !/Chrome|Chromium|Edg|Firefox/i.test(ua)
  );
}

/** `navigator.setAppBadge(n)` where supported; 0 clears it (PUSH-16). */
export async function applyBadge(count: number): Promise<boolean> {
  const n = nav();
  if (!n?.setAppBadge) return false;
  try {
    if (count > 0) await n.setAppBadge(count);
    else if (n.clearAppBadge) await n.clearAppBadge();
    else await n.setAppBadge(0);
    return true;
  } catch {
    return false;
  }
}
