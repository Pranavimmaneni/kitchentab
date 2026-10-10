"use client";

/**
 * Service worker registration and the update flow (PRD §20.1, §21.6; PWA-08, PWA-09, SYNC-22).
 *
 * A new worker installs and waits. The app offers "New version ready · Refresh", but only while
 * nothing is open: no sheet or dialog, no focused field, no write in flight. Nothing reloads by
 * itself; a tap sends SKIP_WAITING and the page reloads when the new worker takes over. A 426
 * STALE_CLIENT from the API shows the refresh prompt whatever is open.
 */
import { useSyncExternalStore } from "react";
import { writesInFlight } from "@/lib/api/signals";

export const SW_URL = process.env.NEXT_PUBLIC_KT_SW_URL || "/sw.js";

type State = { waiting: boolean; staleClient: boolean };
let state: State = { waiting: false, staleClient: false };
const listeners = new Set<() => void>();

function set(patch: Partial<State>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export function updateState(): State {
  return state;
}

export function useUpdateState(): State {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
    () => state,
  );
}

export function markStaleClient(): void {
  set({ staleClient: true });
}

function watch(reg: ServiceWorkerRegistration): void {
  const hasController = () => Boolean(navigator.serviceWorker.controller);
  if (reg.waiting && hasController()) set({ waiting: true });
  reg.addEventListener("updatefound", () => {
    const worker = reg.installing;
    worker?.addEventListener("statechange", () => {
      if (worker.state === "installed" && hasController()) set({ waiting: true });
    });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void reg.update().catch(() => undefined);
  });
}

let registering: Promise<ServiceWorkerRegistration | null> | null = null;

/** Registers the worker once per page (production builds only). */
export function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (registering) return registering;
  registering = (async () => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
    if (process.env.NODE_ENV !== "production") return null;
    try {
      const reg = await navigator.serviceWorker.register(SW_URL, { scope: "/", updateViaCache: "none" });
      watch(reg);
      return reg;
    } catch {
      return null; // blocked (private mode, tests) or unsupported: the app works without it
    }
  })();
  return registering;
}

const EDITABLE = 'input:not([type="checkbox"]):not([type="radio"]):not([type="button"]), textarea, select, [contenteditable="true"]';

/** A sheet, dialog or form field is open, or a write is on its way: don't interrupt (PWA-08). */
export function busyNow(): boolean {
  if (writesInFlight() > 0) return true;
  if (typeof document === "undefined") return false;
  if (document.querySelector('[aria-modal="true"], .kt-sheet')) return true;
  const active = document.activeElement;
  return Boolean(active && active !== document.body && active.matches(EDITABLE));
}

function waitUntilIdle(maxMs: number): Promise<void> {
  const started = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      if (writesInFlight() === 0 || Date.now() - started > maxMs) resolve();
      else setTimeout(tick, 100);
    };
    tick();
  });
}

/** "Refresh": hand over to the waiting worker, then reload once it controls the page. */
export async function applyUpdate(): Promise<void> {
  await waitUntilIdle(10_000);
  const reg = await navigator.serviceWorker?.getRegistration().catch(() => undefined);
  const reload = () => window.location.reload();
  if (!reg) return reload();
  if (!reg.waiting) await reg.update().catch(() => undefined);
  const waiting = reg.waiting;
  if (!waiting) return reload();
  navigator.serviceWorker.addEventListener("controllerchange", reload, { once: true });
  waiting.postMessage({ type: "SKIP_WAITING" });
  setTimeout(reload, 4000); // the new worker never took over: reload anyway
}
