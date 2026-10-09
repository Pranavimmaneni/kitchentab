/**
 * The sign-in hand-off between A or D (send a code) and B (enter it). Kept in sessionStorage so
 * a reload of /verify keeps working; the destination never goes in a URL (privacy rules).
 */
import type { Channel } from "./api/types";

const KEY = "kt-pending-sign-in";

export type PendingSignIn = {
  channel: Channel;
  destination: string;
  masked: string;
  /** Epoch ms after which "Resend code" is allowed. */
  resendAt: number;
  /** Where to go after verifying (a link page finishing a claim), if not the default. */
  returnTo?: string;
};

/** Only same-site paths; never `//host` or absolute URLs (open-redirect guard). */
export function safeReturn(path: string | null | undefined): string | undefined {
  if (!path || !path.startsWith("/") || path.startsWith("//") || path.startsWith("/\\")) {
    return undefined;
  }
  return path;
}

export function savePending(pending: PendingSignIn): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(pending));
  } catch {
    // Private mode without storage: /verify falls back to /start.
  }
}

export function loadPending(): PendingSignIn | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as PendingSignIn;
    return value && typeof value.destination === "string" ? value : null;
  } catch {
    return null;
  }
}

export function clearPending(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}

/** Where B sends people after a successful code (PRD §14A B "After success"). */
export function afterSignIn(pending: Pick<PendingSignIn, "returnTo">, houseCount: number): string {
  const back = safeReturn(pending.returnTo);
  if (back) return back;
  return houseCount === 0 ? "/houses/new" : "/";
}

/** (555) 000-0000 as the person types (US numbers, PRD §14A A). */
export function formatUsPhone(raw: string): string {
  let digits = raw.replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) digits = digits.slice(1);
  digits = digits.slice(0, 10);
  if (digits.length <= 3) return digits.length ? `(${digits}` : "";
  if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}

export function looksLikeEmail(value: string): boolean {
  return /^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$/.test(value.trim());
}

export function looksLikeUsPhone(value: string): boolean {
  const digits = value.replace(/\D/g, "");
  return digits.length === 10 || (digits.length === 11 && digits.startsWith("1"));
}

/** sessionStorage-backed idempotency key per user action, so a reload reuses it. */
export function actionKey(name: string, make: () => string): string {
  const storageKey = `kt-action:${name}`;
  try {
    const existing = sessionStorage.getItem(storageKey);
    if (existing) return existing;
    const key = make();
    sessionStorage.setItem(storageKey, key);
    return key;
  } catch {
    return make();
  }
}

export function clearActionKey(name: string): void {
  try {
    sessionStorage.removeItem(`kt-action:${name}`);
  } catch {
    // ignore
  }
}
