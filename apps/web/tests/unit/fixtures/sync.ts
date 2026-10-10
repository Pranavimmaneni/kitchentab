import type { ChannelFactory, ChannelLike, LockManagerLike } from "../../../lib/sync/leader";
import type { EventSourceLike } from "../../../lib/sync/transport";

/** A scriptable EventSource: tests push `ready`, events, `rollover`, errors. */
export class FakeEventSource implements EventSourceLike {
  static all: FakeEventSource[] = [];
  readonly url: string;
  closed = false;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  private listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.all.push(this);
  }

  static reset(): void {
    FakeEventSource.all = [];
  }

  static open(): FakeEventSource[] {
    return FakeEventSource.all.filter((s) => !s.closed);
  }

  static last(): FakeEventSource {
    const last = FakeEventSource.all[FakeEventSource.all.length - 1];
    if (!last) throw new Error("no EventSource was opened");
    return last;
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, data: unknown): void {
    const message = { data: JSON.stringify(data) } as MessageEvent<string>;
    if (type === "message") this.onmessage?.(message);
    else this.listeners.get(type)?.forEach((l) => l(message));
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }

  close(): void {
    this.closed = true;
  }
}

/** Web Locks with one exclusive holder per name and a FIFO queue, like the browser's. */
export function fakeLocks(): LockManagerLike & { holder: (name: string) => number | undefined } {
  type Waiter = { id: number; cb: (lock: unknown) => Promise<void> | void; resolve: () => void; reject: (e: unknown) => void };
  const queues = new Map<string, Waiter[]>();
  const held = new Map<string, number>();
  let next = 0;
  function pump(name: string): void {
    if (held.has(name)) return;
    const waiter = queues.get(name)?.shift();
    if (!waiter) return;
    held.set(name, waiter.id);
    Promise.resolve()
      .then(() => waiter.cb({ name }))
      .finally(() => {
        held.delete(name);
        waiter.resolve();
        pump(name);
      });
  }
  return {
    holder: (name) => held.get(name),
    request(name, options, cb) {
      return new Promise<void>((resolve, reject) => {
        const waiter: Waiter = { id: ++next, cb, resolve, reject };
        queues.set(name, [...(queues.get(name) ?? []), waiter]);
        options.signal?.addEventListener("abort", () => {
          const q = queues.get(name) ?? [];
          if (q.includes(waiter)) {
            queues.set(
              name,
              q.filter((w) => w !== waiter),
            );
            reject(new DOMException("aborted", "AbortError"));
          }
        });
        pump(name);
      });
    },
  };
}

/** BroadcastChannel between "tabs": delivers to every other channel of the same name. */
export function fakeChannels(): ChannelFactory {
  const hub = new Map<string, Set<ChannelLike>>();
  return (name) => {
    const peers = hub.get(name) ?? new Set<ChannelLike>();
    hub.set(name, peers);
    const channel: ChannelLike = {
      onmessage: null,
      postMessage(message) {
        const copy = structuredClone(message);
        for (const peer of peers) {
          if (peer !== channel) queueMicrotask(() => peer.onmessage?.({ data: copy } as MessageEvent));
        }
      },
      close() {
        peers.delete(channel);
      },
    };
    peers.add(channel);
    return channel;
  };
}

export const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
