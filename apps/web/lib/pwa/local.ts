/**
 * Per-device memory for install and notification prompts (PRD §20.1–20.2): "a card appears at
 * most twice per device", "denied → never ask again on that device", "Not now snoozes 14 days".
 * localStorage, wrapped so private mode or a full disk never breaks the app.
 */

export const KEYS = {
  visits: "kt.pwa.visits",
  visitSession: "kt.pwa.visit",
  installShown: "kt.pwa.installShown",
  n1NotNow: "kt.pwa.n1NotNow",
  n1SnoozedUntil: "kt.pwa.n1SnoozedUntil",
  pushDenied: "kt.pwa.pushDenied",
  endpoint: "kt.pwa.endpoint",
  standaloneSignedIn: "kt.pwa.standaloneSignedIn",
  standaloneOffered: "kt.pwa.standaloneOffered",
  tracked: "kt.pwa.tracked.",
} as const;

export const SNOOZE_MS = 14 * 24 * 60 * 60 * 1000;
export const MAX_INSTALL_CARDS = 2;
export const MAX_NOT_NOW = 2;

function store(kind: "local" | "session"): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

export function getString(key: string, kind: "local" | "session" = "local"): string | null {
  try {
    return store(kind)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function setString(
  key: string,
  value: string | null,
  kind: "local" | "session" = "local",
): void {
  try {
    const s = store(kind);
    if (!s) return;
    if (value === null) s.removeItem(key);
    else s.setItem(key, value);
  } catch {
    // storage full or blocked: forget it
  }
}

export function getNumber(key: string): number {
  const n = Number(getString(key));
  return Number.isFinite(n) ? n : 0;
}

export function setNumber(key: string, value: number): void {
  setString(key, String(value));
}

/** Counts one visit per browser session (a new tab or app launch is a visit; reloads are not). */
export function countVisit(): number {
  if (getString(KEYS.visitSession, "session")) return getNumber(KEYS.visits);
  setString(KEYS.visitSession, "1", "session");
  const visits = getNumber(KEYS.visits) + 1;
  setNumber(KEYS.visits, visits);
  return visits;
}

export type PromptMemory = {
  installShown: number;
  n1NotNow: number;
  n1SnoozedUntil: number;
  pushDenied: boolean;
};

export function readPromptMemory(): PromptMemory {
  return {
    installShown: getNumber(KEYS.installShown),
    n1NotNow: getNumber(KEYS.n1NotNow),
    n1SnoozedUntil: getNumber(KEYS.n1SnoozedUntil),
    pushDenied: getString(KEYS.pushDenied) === "1",
  };
}

/** "Not now": snooze 14 days; the second one means only Settings offers it again. */
export function rememberNotNow(now = Date.now()): void {
  setNumber(KEYS.n1NotNow, getNumber(KEYS.n1NotNow) + 1);
  setNumber(KEYS.n1SnoozedUntil, now + SNOOZE_MS);
}

export function rememberInstallShown(): void {
  setNumber(KEYS.installShown, getNumber(KEYS.installShown) + 1);
}

export function rememberDenied(): void {
  setString(KEYS.pushDenied, "1");
}
