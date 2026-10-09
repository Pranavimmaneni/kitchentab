import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COALESCE_MS, createTickCoalescer, type TickChange } from "../../lib/split/coalesce";

const cell = (on: boolean, itemId = "milk", participant = "m:arjun"): TickChange => ({ itemId, participant, on });

describe("tick coalescing", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("SYNC-30 tick-untick-tick within 250 ms sends one request with the final state on", () => {
    const send = vi.fn();
    const q = createTickCoalescer({ send });
    q.set(cell(true), false);
    vi.advanceTimersByTime(80);
    q.set(cell(false), false);
    vi.advanceTimersByTime(80);
    q.set(cell(true), false);
    vi.advanceTimersByTime(COALESCE_MS - 1);
    expect(send).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith([cell(true)]);
    vi.advanceTimersByTime(5_000);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("a double tap that ends where it started sends nothing and settles the cell", () => {
    const send = vi.fn();
    const onSettled = vi.fn();
    const q = createTickCoalescer({ send, onSettled });
    q.set(cell(true), false);
    q.set(cell(false), false);
    vi.advanceTimersByTime(COALESCE_MS);
    expect(send).not.toHaveBeenCalled();
    expect(onSettled).toHaveBeenCalledWith([cell(false)]);
    expect(q.size()).toBe(0);
  });

  it("cells that come due together go out as one batch; later cells wait for their own window", () => {
    const send = vi.fn();
    const q = createTickCoalescer({ send });
    q.set(cell(true, "milk"), false);
    q.set(cell(true, "eggs"), false);
    vi.advanceTimersByTime(100);
    q.set(cell(true, "rice"), false);
    vi.advanceTimersByTime(COALESCE_MS - 100);
    expect(send).toHaveBeenNthCalledWith(1, [cell(true, "milk"), cell(true, "eggs")]);
    expect(q.isPending("rice", "m:arjun")).toBe(true);
    vi.advanceTimersByTime(100);
    expect(send).toHaveBeenNthCalledWith(2, [cell(true, "rice")]);
  });

  it("flush sends everything at once (column toggles, undo, leaving the screen)", () => {
    const send = vi.fn();
    const q = createTickCoalescer({ send });
    q.set(cell(true, "milk"), false);
    q.set(cell(false, "eggs"), true);
    q.flush();
    expect(send).toHaveBeenCalledWith([cell(true, "milk"), cell(false, "eggs")]);
    vi.advanceTimersByTime(COALESCE_MS);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("keeps the first baseline for a cell while it is pending", () => {
    const send = vi.fn();
    const q = createTickCoalescer({ send });
    q.set(cell(false), true);
    q.set(cell(true), false); // the caller's view changed; the cell still started "on"
    vi.advanceTimersByTime(COALESCE_MS);
    expect(send).not.toHaveBeenCalled();
  });
});
