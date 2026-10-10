"use client";

import { useSyncExternalStore } from "react";

/**
 * Connection state for the offline rules (PRD §14A Offline, OFFLINE-01, OFFLINE-04): while
 * offline every write control is disabled with "Needs a connection"; nothing is queued.
 */
function subscribe(listener: () => void): () => void {
  window.addEventListener("online", listener);
  window.addEventListener("offline", listener);
  return () => {
    window.removeEventListener("online", listener);
    window.removeEventListener("offline", listener);
  };
}

export function isOnline(): boolean {
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

/** True while the browser has a connection (server render assumes online). */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, isOnline, () => true);
}
