"use client";

import { useCallback, useSyncExternalStore } from "react";
import { useMe } from "./api/hooks";
import type { MyHouse } from "./api/types";

/**
 * The house the signed-in screens show (FR-08): the last one picked in the switcher on this
 * device, else the most recently joined. Stored per device only; nothing about access is decided
 * here (the API checks membership on every request).
 */
export const HOUSE_STORAGE_KEY = "kt.house";

const listeners = new Set<() => void>();

function read(): string | null {
  try {
    return window.localStorage.getItem(HOUSE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export function rememberHouse(id: string): void {
  try {
    window.localStorage.setItem(HOUSE_STORAGE_KEY, id);
  } catch {
    // Private mode: the pick still applies until reload.
  }
  listeners.forEach((listener) => listener());
}

/** Back to the default (most recently joined), e.g. before starting another house. */
export function forgetHouse(): void {
  try {
    window.localStorage.removeItem(HOUSE_STORAGE_KEY);
  } catch {
    // ignore
  }
  listeners.forEach((listener) => listener());
}

/** Picks the stored house when the user still belongs to it, else the first one listed. */
export function pickHouse(houses: readonly MyHouse[], stored: string | null): MyHouse | null {
  return houses.find((h) => h.id === stored) ?? houses[0] ?? null;
}

export function useCurrentHouse() {
  const me = useMe();
  const stored = useSyncExternalStore(subscribe, read, () => null);
  const houses = me.data?.houses ?? [];
  const house = pickHouse(houses, stored);
  const select = useCallback((id: string) => rememberHouse(id), []);
  return { me, houses, house, select };
}
