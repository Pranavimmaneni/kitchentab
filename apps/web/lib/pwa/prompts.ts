"use client";

/**
 * The two prompts the shell can show (PRD §20.1–20.2): the N1 sheet ("Turn on notifications" / the
 * iPhone install steps) and the install card, plus the captured `beforeinstallprompt` event. Small
 * stores so Settings › Notifications can open N1 and the install button can reuse the event.
 */
import { useSyncExternalStore } from "react";

type BeforeInstallPrompt = Event & {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

export type N1State = { platform: "ios" | "android"; countsAsInstall: boolean; fromSettings: boolean } | null;

type State = { n1: N1State; installCard: boolean; canInstall: boolean; shareArrow: boolean };

let state: State = { n1: null, installCard: false, canInstall: false, shareArrow: false };
let deferred: BeforeInstallPrompt | null = null;
const listeners = new Set<() => void>();

function set(patch: Partial<State>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export function promptState(): State {
  return state;
}

export function usePrompts(): State {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
    () => state,
  );
}

export function openN1(n1: NonNullable<N1State>): void {
  set({ n1, installCard: false });
}

export function closeN1(): void {
  set({ n1: null });
}

export function showInstallCard(): void {
  set({ installCard: true });
}

export function hideInstallCard(): void {
  set({ installCard: false });
}

export function showShareArrow(on: boolean): void {
  set({ shareArrow: on });
}

/** `beforeinstallprompt` (Chromium): keep it for the card and Settings (PWA-05). */
export function captureInstallPrompt(event: Event): void {
  event.preventDefault();
  deferred = event as BeforeInstallPrompt;
  set({ canInstall: true });
}

export function installed(): void {
  deferred = null;
  set({ canInstall: false, installCard: false });
}

/** Shows the browser's install dialog; the event can be used once. */
export async function promptInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const event = deferred;
  if (!event) return "unavailable";
  deferred = null;
  set({ canInstall: false, installCard: false });
  try {
    await event.prompt();
    return (await event.userChoice).outcome;
  } catch {
    return "dismissed";
  }
}
