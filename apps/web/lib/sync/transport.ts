/**
 * The house stream connection (PRD §21.5): one EventSource on `/houses/{id}/events`, resumed
 * from the last applied sequence number with `?last_event_id=` (EventSource cannot set the
 * `Last-Event-ID` header on a fresh connection; the server accepts either).
 *
 * - `ready {seq}` opens the stream; `rollover` (the server's 270 s limit) reconnects at once;
 *   `resync` and `revoked` are passed up.
 * - Errors close the stream and reconnect with backoff 1, 2, 4 … 30 s with jitter.
 * - After 3 failed connects in a row, `poll()` runs every 15 s until a stream opens again
 *   (SYNC-34); the first `ready` stops polling.
 */
import type { HouseEvent } from "./types";

export const BACKOFF_BASE_MS = 1000;
export const BACKOFF_MAX_MS = 30_000;
export const FAILURES_BEFORE_POLLING = 3;
export const POLL_INTERVAL_MS = 15_000;

/** The subset of EventSource we use, so tests can pass a fake. */
export type EventSourceLike = {
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
  onerror: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  close(): void;
};
export type EventSourceFactory = (url: string) => EventSourceLike;

export type TransportStatus = "connecting" | "open" | "waiting" | "stopped";

export type TransportOptions = {
  /** Stream URL for a given resume point (null = start from now). */
  url: (lastSeq: number | null) => string;
  lastSeq: () => number | null;
  onReady: (seq: number) => void;
  onEvent: (event: HouseEvent) => void;
  onResync: (seq: number | null) => void;
  /** `reason` is "not_a_member" (removed from the house) or "signed_out" (session revoked). */
  onRevoked: (reason: string | null) => void;
  /** The JSON catch-up (`?after=`), used by the polling fallback. */
  poll: () => Promise<void>;
  createEventSource?: EventSourceFactory;
  random?: () => number;
  pollIntervalMs?: number;
  onStatus?: (status: TransportStatus, info: { failures: number; polling: boolean }) => void;
};

/** Backoff before reconnect number `failures` (1-based): 1, 2, 4 … 30 s, ±20% jitter. */
export function backoffDelay(failures: number, random: () => number = Math.random): number {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, failures - 1));
  return Math.round(Math.min(BACKOFF_MAX_MS, base * (0.8 + 0.4 * random())));
}

function parse<T>(event: MessageEvent<string>): T | null {
  try {
    return JSON.parse(event.data) as T;
  } catch {
    return null;
  }
}

export class StreamTransport {
  private source: EventSourceLike | null = null;
  private failures = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private stopped = true;
  private status: TransportStatus = "stopped";

  constructor(private readonly options: TransportOptions) {}

  get polling(): boolean {
    return this.pollTimer !== null;
  }

  get failureCount(): number {
    return this.failures;
  }

  get state(): TransportStatus {
    return this.status;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.closeSource();
    this.clearReconnect();
    this.stopPolling();
    this.setStatus("stopped");
  }

  /** Back online or visible again: skip the remaining backoff and reconnect now. */
  kick(): void {
    if (this.stopped || this.source) return;
    this.clearReconnect();
    this.connect();
  }

  private setStatus(status: TransportStatus): void {
    this.status = status;
    this.options.onStatus?.(status, { failures: this.failures, polling: this.polling });
  }

  private connect(): void {
    if (this.stopped) return;
    this.closeSource();
    const create =
      this.options.createEventSource ??
      ((url: string) =>
        new EventSource(url, { withCredentials: true }) as unknown as EventSourceLike);
    let source: EventSourceLike;
    try {
      source = create(this.options.url(this.options.lastSeq()));
    } catch {
      this.failed();
      return;
    }
    this.source = source;
    this.setStatus("connecting");

    source.addEventListener("ready", (message) => {
      if (this.source !== source) return;
      this.failures = 0;
      this.stopPolling();
      this.setStatus("open");
      const body = parse<{ seq: number }>(message);
      if (body && Number.isInteger(body.seq)) this.options.onReady(body.seq);
    });
    source.addEventListener("resync", (message) => {
      if (this.source !== source) return;
      this.failures = 0;
      this.stopPolling();
      this.setStatus("open");
      const body = parse<{ seq: number }>(message);
      this.options.onResync(body && Number.isInteger(body.seq) ? body.seq : null);
    });
    source.addEventListener("rollover", () => {
      if (this.source !== source) return;
      // Planned close at the server's time limit: resume from the last applied seq right away.
      this.closeSource();
      this.connect();
    });
    source.addEventListener("revoked", (message) => {
      if (this.source !== source) return;
      this.stop();
      const body = parse<{ reason?: unknown }>(message);
      this.options.onRevoked(typeof body?.reason === "string" ? body.reason : null);
    });
    source.onmessage = (message) => {
      if (this.source !== source) return;
      const event = parse<HouseEvent>(message);
      if (event && Number.isInteger(event.seq)) this.options.onEvent(event);
    };
    source.onerror = () => {
      if (this.source !== source) return;
      this.failed();
    };
  }

  private failed(): void {
    this.closeSource();
    if (this.stopped) return;
    this.failures += 1;
    if (this.failures >= FAILURES_BEFORE_POLLING) this.startPolling();
    this.setStatus("waiting");
    this.clearReconnect();
    this.reconnectTimer = setTimeout(
      () => {
        this.reconnectTimer = null;
        this.connect();
      },
      backoffDelay(this.failures, this.options.random),
    );
  }

  private closeSource(): void {
    const source = this.source;
    this.source = null;
    if (source) {
      source.onerror = null;
      source.onmessage = null;
      source.close();
    }
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private startPolling(): void {
    if (this.pollTimer !== null || this.stopped) return;
    const run = () => {
      void this.options.poll().catch(() => undefined);
    };
    this.pollTimer = setInterval(run, this.options.pollIntervalMs ?? POLL_INTERVAL_MS);
    run();
  }

  private stopPolling(): void {
    if (this.pollTimer !== null) clearInterval(this.pollTimer);
    this.pollTimer = null;
  }
}
