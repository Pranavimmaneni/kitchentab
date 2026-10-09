/**
 * Tick coalescing (PRD §21.6 "Rapid taps"): writes to the same split cell are held for 250 ms
 * after the last tap and only the final state is sent, so double taps and tick-untick-tick never
 * race each other. A cell whose final state equals the state it started from sends nothing.
 * Cells that come due together go out as one set-state `PUT /allocations` batch.
 */

export type TickChange = { itemId: string; participant: string; on: boolean };

export const COALESCE_MS = 250;

export function cellKey(itemId: string, participant: string): string {
  return `${itemId}|${participant}`;
}

type Pending = { change: TickChange; confirmed: boolean; due: number };

export type TickCoalescerOptions = {
  delayMs?: number;
  now?: () => number;
  /** Called with every cell that came due and differs from where it started. */
  send: (changes: TickChange[]) => void;
  /** Called for cells that ended where they started (nothing to send). */
  onSettled?: (changes: TickChange[]) => void;
};

export type TickCoalescer = {
  /**
   * Records the desired state of a cell. `confirmed` is the state the server has (or will have
   * from a write already in flight) — the baseline a net no-op is measured against.
   */
  set: (change: TickChange, confirmed: boolean) => void;
  /** Sends everything now (leaving the screen, column toggles, undo). */
  flush: () => void;
  isPending: (itemId: string, participant: string) => boolean;
  size: () => number;
  dispose: () => void;
};

export function createTickCoalescer({
  delayMs = COALESCE_MS,
  now = () => Date.now(),
  send,
  onSettled,
}: TickCoalescerOptions): TickCoalescer {
  const pending = new Map<string, Pending>();
  let timer: ReturnType<typeof setTimeout> | null = null;

  function schedule(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (pending.size === 0) return;
    let next = Infinity;
    for (const p of pending.values()) next = Math.min(next, p.due);
    timer = setTimeout(() => {
      timer = null;
      release(false);
    }, Math.max(0, next - now()));
  }

  function release(all: boolean): void {
    const t = now();
    const out: TickChange[] = [];
    const settled: TickChange[] = [];
    for (const [key, p] of pending) {
      if (!all && p.due > t) continue;
      pending.delete(key);
      (p.change.on === p.confirmed ? settled : out).push(p.change);
    }
    if (settled.length) onSettled?.(settled);
    if (out.length) send(out);
    schedule();
  }

  return {
    set(change, confirmed) {
      const key = cellKey(change.itemId, change.participant);
      const existing = pending.get(key);
      pending.set(key, {
        change,
        confirmed: existing ? existing.confirmed : confirmed,
        due: now() + delayMs,
      });
      schedule();
    },
    flush() {
      release(true);
    },
    isPending(itemId, participant) {
      return pending.has(cellKey(itemId, participant));
    },
    size() {
      return pending.size;
    },
    dispose() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      pending.clear();
    },
  };
}
