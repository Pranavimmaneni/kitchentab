import { afterEach, describe, expect, it, vi } from "vitest";
import { ageLabel } from "../../lib/kitchen";
import { dayOpen } from "../../lib/open-bill";
import {
  noteServerDate,
  onServerOffset,
  resetServerOffset,
  serverNow,
  serverOffset,
} from "../../lib/server-time";

const MIN = 60_000;

describe("server time (PRD §21.6 Time)", () => {
  afterEach(() => {
    resetServerOffset();
    vi.useRealTimers();
  });

  it("CHAOS-08 a device 10 minutes fast or slow keeps the offset from the Date header", () => {
    vi.useFakeTimers();
    const server = Date.UTC(2026, 9, 9, 18, 0, 0);
    for (const skew of [10 * MIN, -10 * MIN]) {
      resetServerOffset();
      vi.setSystemTime(server + skew);
      const sent = Date.now();
      noteServerDate(new Date(server).toUTCString(), sent, sent + 200);
      // Within the Date header's one-second resolution.
      expect(Math.abs(serverOffset() + skew)).toBeLessThanOrEqual(1000);
      expect(Math.abs(serverNow() - server)).toBeLessThanOrEqual(1000);
    }
  });

  it("CHAOS-08 relative times read the same on a skewed device once they use server now", () => {
    vi.useFakeTimers();
    const server = Date.UTC(2026, 9, 9, 18, 0, 0);
    vi.setSystemTime(server + 10 * MIN);
    noteServerDate(new Date(server).toUTCString(), Date.now(), Date.now());
    const created = new Date(server - 30_000).toISOString();
    expect(ageLabel(created, new Date(Date.now()))).toBe("10 MIN AGO"); // the device clock lies
    expect(ageLabel(created, new Date(serverNow()))).toBe("JUST NOW");
    const sent = new Date(server - 2 * 86_400_000 + 5 * MIN).toISOString();
    expect(dayOpen(sent, serverNow())).toBe(2);
  });

  it("ignores a missing or broken Date header and only announces real jumps", () => {
    const heard = vi.fn();
    const off = onServerOffset(heard);
    noteServerDate(null, 0, 0);
    noteServerDate("not a date", 0, 0);
    expect(serverOffset()).toBe(0);
    expect(heard).not.toHaveBeenCalled();
    const now = Date.now();
    noteServerDate(new Date(now + 5 * MIN).toUTCString(), now, now);
    expect(heard).toHaveBeenCalledTimes(1);
    // Jitter inside the header's resolution is not news.
    noteServerDate(new Date(now + 5 * MIN).toUTCString(), now + 300, now + 300);
    expect(heard).toHaveBeenCalledTimes(1);
    off();
  });
});
