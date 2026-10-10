/**
 * One stream per browser (PRD §21.6 "Several tabs", SYNC-20, SYNC-31): tabs of the same house
 * queue on a Web Lock; the holder is the leader, opens the stream and relays every applied event
 * over a BroadcastChannel. When the leader tab closes, the browser releases its lock and the next
 * tab in the queue takes over at once. Without Web Locks or BroadcastChannel, every tab leads.
 */
import type { RelayMessage } from "./types";

export type LockManagerLike = {
  request(
    name: string,
    options: { signal?: AbortSignal },
    callback: (lock: unknown) => Promise<void> | void,
  ): Promise<unknown>;
};

export type ChannelLike = {
  postMessage(message: unknown): void;
  close(): void;
  onmessage: ((event: MessageEvent) => void) | null;
};

export type ChannelFactory = (name: string) => ChannelLike;

export function defaultLocks(): LockManagerLike | undefined {
  if (typeof navigator === "undefined") return undefined;
  const locks = (navigator as Navigator & { locks?: LockManagerLike }).locks;
  return locks && typeof locks.request === "function" ? locks : undefined;
}

export function defaultChannel(): ChannelFactory | undefined {
  if (typeof BroadcastChannel === "undefined") return undefined;
  return (name) => new BroadcastChannel(name) as unknown as ChannelLike;
}

/**
 * Runs `lead` while this tab holds the lock `name`; `lead` returns its own cleanup. Returns a
 * dispose function that leaves the queue (or steps down and releases the lock).
 */
export function runAsLeader(
  name: string,
  lead: () => () => void,
  locks: LockManagerLike | undefined = defaultLocks(),
): () => void {
  if (!locks) {
    const stop = lead();
    return stop;
  }
  const controller = new AbortController();
  let disposed = false;
  let stepDown: (() => void) | null = null;
  let release: (() => void) | null = null;
  locks
    .request(name, { signal: controller.signal }, () => {
      if (disposed) return;
      stepDown = lead();
      return new Promise<void>((resolve) => {
        release = resolve;
      });
    })
    .catch(() => undefined); // AbortError: disposed while still waiting in the queue
  return () => {
    if (disposed) return;
    disposed = true;
    controller.abort();
    stepDown?.();
    release?.();
  };
}

/** The relay between tabs of one house. A no-op without BroadcastChannel. */
export function openRelay(
  name: string,
  onMessage: (message: RelayMessage) => void,
  channel: ChannelFactory | undefined = defaultChannel(),
): { post: (message: RelayMessage) => void; close: () => void; available: boolean } {
  if (!channel) return { post: () => undefined, close: () => undefined, available: false };
  const ch = channel(name);
  ch.onmessage = (event) => onMessage(event.data as RelayMessage);
  return {
    post: (message) => ch.postMessage(message),
    close: () => {
      ch.onmessage = null;
      ch.close();
    },
    available: true,
  };
}
