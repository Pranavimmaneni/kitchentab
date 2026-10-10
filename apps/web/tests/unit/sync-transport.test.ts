import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BACKOFF_MAX_MS,
  POLL_INTERVAL_MS,
  StreamTransport,
  backoffDelay,
  type TransportOptions,
} from "../../lib/sync/transport";
import type { HouseEvent } from "../../lib/sync/types";
import { FakeEventSource } from "./fixtures/sync";

function transport(overrides: Partial<TransportOptions> = {}) {
  let last: number | null = null;
  const events: HouseEvent[] = [];
  const options: TransportOptions = {
    url: (seq) => `/api/v1/houses/h1/events${seq === null ? "" : `?last_event_id=${seq}`}`,
    lastSeq: () => last,
    onReady: vi.fn((seq: number) => {
      last ??= seq;
    }),
    onEvent: (e) => {
      events.push(e);
      last = e.seq;
    },
    onResync: vi.fn(),
    onRevoked: vi.fn(),
    poll: vi.fn(async () => undefined),
    createEventSource: (url) => new FakeEventSource(url),
    random: () => 0.5,
    ...overrides,
  };
  return { t: new StreamTransport(options), options, events, setLast: (n: number) => (last = n) };
}

const ev = (seq: number): HouseEvent => ({
  seq,
  type: "allocation_changed",
  aggregate: { type: "bill", id: "b1", version: 1 },
  actor_member_id: null,
  client_mutation_id: null,
  data: null,
});

describe("house stream transport (PRD §21.5)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.reset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("SYNC-21 reconnects with backoff 1, 2, 4 … 30 s with jitter after errors", () => {
    expect([1, 2, 3, 4, 5, 6, 7].map((n) => backoffDelay(n, () => 0.5))).toEqual([
      1000, 2000, 4000, 8000, 16000, 30000, 30000,
    ]);
    // ±20% jitter, never above 30 s.
    expect(backoffDelay(1, () => 0)).toBe(800);
    expect(backoffDelay(1, () => 1)).toBe(1200);
    expect(backoffDelay(9, () => 1)).toBe(BACKOFF_MAX_MS);

    const { t } = transport();
    t.start();
    FakeEventSource.last().fail();
    expect(FakeEventSource.open()).toHaveLength(0);
    vi.advanceTimersByTime(999);
    expect(FakeEventSource.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.all).toHaveLength(2);
    t.stop();
  });

  it("SYNC-34 polls ?after= every 15 s after 3 failed connects and stops when the stream recovers", async () => {
    const { t, options } = transport();
    t.start();
    for (let n = 1; n <= 2; n++) {
      FakeEventSource.last().fail();
      expect(t.polling).toBe(false);
      vi.advanceTimersByTime(backoffDelay(n, () => 0.5));
    }
    FakeEventSource.last().fail(); // third failure in a row
    expect(t.polling).toBe(true);
    expect(options.poll).toHaveBeenCalledTimes(1); // right away
    // The reconnect timer keeps trying in the background (4 s) while polling continues.
    vi.advanceTimersByTime(backoffDelay(3, () => 0.5));
    const recovered = FakeEventSource.last();
    expect(recovered.closed).toBe(false);
    vi.advanceTimersByTime(POLL_INTERVAL_MS - backoffDelay(3, () => 0.5));
    expect(options.poll).toHaveBeenCalledTimes(2);
    // When a stream opens, polling stops.
    recovered.emit("ready", { seq: 7 });
    expect(t.polling).toBe(false);
    vi.advanceTimersByTime(POLL_INTERVAL_MS * 3);
    expect(options.poll).toHaveBeenCalledTimes(2);
    t.stop();
  });

  it("VERCEL-01 rollover at the server's 270 s limit reconnects at once from the last applied seq [SYNC-17]", () => {
    const { t, events } = transport();
    t.start();
    const first = FakeEventSource.last();
    expect(first.url).toBe("/api/v1/houses/h1/events");
    first.emit("ready", { seq: 4 });
    first.emit("message", ev(5));
    first.emit("message", ev(6));
    first.emit("rollover", { seq: 6 });
    expect(first.closed).toBe(true);
    const second = FakeEventSource.last();
    expect(second).not.toBe(first);
    expect(second.url).toBe("/api/v1/houses/h1/events?last_event_id=6");
    expect(events.map((e) => e.seq)).toEqual([5, 6]);
    t.stop();
  });

  it("SYNC-33 a revoked stream closes for good and reports it [SYNC-12]", () => {
    const { t, options } = transport();
    t.start();
    FakeEventSource.last().emit("revoked", { reason: "not_a_member" });
    expect(options.onRevoked).toHaveBeenCalledTimes(1);
    expect(options.onRevoked).toHaveBeenCalledWith("not_a_member");
    expect(t.state).toBe("stopped");
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
  });

  it("AUTH-09 a stream closed because the session was signed out everywhere says so [FR-01]", () => {
    const { t, options } = transport();
    t.start();
    FakeEventSource.last().emit("revoked", { reason: "signed_out" });
    expect(options.onRevoked).toHaveBeenCalledWith("signed_out");
    expect(t.state).toBe("stopped");
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
  });

  it("SYNC-27 resync from the server is passed up with its seq", () => {
    const { t, options } = transport();
    t.start();
    FakeEventSource.last().emit("resync", { seq: 900 });
    expect(options.onResync).toHaveBeenCalledWith(900);
    t.stop();
  });

  it("OFFLINE-02 coming back online skips the remaining backoff (catch-up trigger)", () => {
    const { t } = transport();
    t.start();
    FakeEventSource.last().fail();
    expect(FakeEventSource.open()).toHaveLength(0);
    t.kick();
    expect(FakeEventSource.open()).toHaveLength(1);
    t.stop();
    expect(FakeEventSource.open()).toHaveLength(0);
  });
});
