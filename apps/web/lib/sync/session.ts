/**
 * Sign-out reaches every tab (PRD §21.6 "Sign-out and session revocation broadcast to every tab",
 * AUTH-09). Three ways in, one way out:
 *
 * - this tab signed out (Settings) → `announceSignedOut()` tells this tab and, over a
 *   BroadcastChannel, every other tab of this browser;
 * - another device chose "Sign out everywhere" → the house stream answers
 *   `revoked {reason: "signed_out"}` within a second, and the tab announces it;
 * - any request answered 401 (`kt:unauthorized` from the API client) → this tab re-reads `/me`.
 *
 * The signed-in shell listens with `onSignedOut` and goes to sign-in.
 */
const CHANNEL = "kt-session";

type Listener = () => void;
const listeners = new Set<Listener>();
let channel: BroadcastChannel | null | undefined;

function notify(): void {
  listeners.forEach((listener) => listener());
}

function sessionChannel(): BroadcastChannel | null {
  if (channel !== undefined) return channel;
  if (typeof BroadcastChannel === "undefined") {
    channel = null;
    return channel;
  }
  channel = new BroadcastChannel(CHANNEL);
  channel.onmessage = (event: MessageEvent) => {
    if ((event.data as { kind?: string } | null)?.kind === "signed-out") notify();
  };
  return channel;
}

export function onSignedOut(listener: Listener): () => void {
  sessionChannel();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** This browser's session is gone: every tab goes to sign-in. */
export function announceSignedOut(): void {
  sessionChannel()?.postMessage({ kind: "signed-out" });
  notify();
}
