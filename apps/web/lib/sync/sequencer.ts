/**
 * In-order, gapless application of house events (PRD §21.5 "Gaps", SYNC-17, SYNC-18).
 *
 * Events are applied strictly in `seq` order. Duplicates (replays after a reconnect, a relay
 * racing a catch-up) are dropped. When an event arrives with `seq > last + 1`, it is held and the
 * missing range is fetched with `GET ?after=last` before anything later is applied. A catch-up
 * that answers `resync` (more than 500 events or 7 days behind) hands over to `onResync`, which
 * refetches everything cached.
 */
import type { CatchUp, HouseEvent } from "./types";

export type SequencerOptions = {
  fetchAfter: (after: number) => Promise<CatchUp>;
  apply: (event: HouseEvent) => void;
  onResync: () => void;
  /** A catch-up failed (network); the caller decides (e.g. NOT_A_MEMBER → revoked). */
  onError?: (error: unknown) => void;
  /** Gap fills that return nothing before we give up and resync (default 3). */
  maxEmptyFills?: number;
  /** Wait between two gap fills that found nothing (default 300 ms). */
  emptyFillDelayMs?: number;
};

export class Sequencer {
  private last: number | null = null;
  private readonly pending = new Map<number, HouseEvent>();
  private filling: Promise<void> | null = null;
  private emptyFills = 0;
  private stopped = false;

  constructor(private readonly options: SequencerOptions) {}

  /** The last applied sequence number (null until the first `ready` or event). */
  get lastSeq(): number | null {
    return this.last;
  }

  /** Events held back waiting for a gap to fill. */
  get held(): number {
    return this.pending.size;
  }

  /** Sets the baseline (`ready` on a fresh stream, or after a resync). */
  reset(seq: number | null): void {
    this.last = seq;
    this.pending.clear();
    this.emptyFills = 0;
  }

  stop(): void {
    this.stopped = true;
    this.pending.clear();
  }

  /** One event from the stream, the relay or a catch-up. */
  receive(event: HouseEvent): void {
    if (this.stopped) return;
    // A follower tab's first relayed event (or a stream that skipped `ready`) sets the baseline.
    if (this.last === null) this.last = event.seq - 1;
    if (event.seq <= this.last || this.pending.has(event.seq)) return;
    this.pending.set(event.seq, event);
    this.drain();
    if (this.pending.size > 0) void this.fill();
  }

  /**
   * `GET ?after=last` now: catch-up triggers (focus, online, visible, reconnect) and the polling
   * fallback. Resolves once the fetched events are applied.
   */
  catchUp(): Promise<void> {
    if (this.stopped || this.last === null) return Promise.resolve();
    return this.fill();
  }

  private drain(): void {
    while (this.last !== null && this.pending.has(this.last + 1)) {
      const next = this.pending.get(this.last + 1)!;
      this.pending.delete(next.seq);
      this.last = next.seq;
      this.options.apply(next);
      if (this.stopped) return;
    }
  }

  private fill(): Promise<void> {
    this.filling ??= this.runFill().finally(() => {
      this.filling = null;
    });
    return this.filling;
  }

  private async runFill(): Promise<void> {
    const maxEmpty = this.options.maxEmptyFills ?? 3;
    while (!this.stopped && this.last !== null) {
      const from = this.last;
      let page: CatchUp;
      try {
        page = await this.options.fetchAfter(from);
      } catch (error) {
        this.options.onError?.(error);
        return; // held events wait for the next event or catch-up trigger
      }
      if (this.stopped) return;
      if (page.resync) {
        this.reset(null);
        this.options.onResync();
        return;
      }
      for (const event of page.events) {
        if (this.last !== null && event.seq > this.last && !this.pending.has(event.seq)) {
          this.pending.set(event.seq, event);
        }
      }
      this.drain();
      const progressed = this.last !== from;
      if (this.pending.size === 0) {
        this.emptyFills = 0;
        // A full page (500) may have more behind it.
        if (page.events.length >= 500 && progressed) continue;
        return;
      }
      if (progressed) {
        this.emptyFills = 0;
        continue;
      }
      // The server has nothing for a gap we can see: never wait forever, rebuild instead.
      if (++this.emptyFills >= maxEmpty) {
        this.reset(null);
        this.options.onResync();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, this.options.emptyFillDelayMs ?? 300));
    }
  }
}
