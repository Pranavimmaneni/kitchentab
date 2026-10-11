import { describe, expect, it } from "vitest";

import {
  FLY_MS,
  LIFT_MS,
  flyStart,
  landAt,
  scatter,
  stagger,
  totalMs,
} from "@/features/kitchen/stocking/timing";

describe("stocking animation timing (SPEC §7)", () => {
  it("ANIM-01 eight items finish within 520 ms, the last landing at about 420 ms [FR-G10]", () => {
    expect(stagger(8)).toBe(20);
    expect(flyStart(0, 8)).toBe(LIFT_MS);
    expect(landAt(7, 8)).toBe(LIFT_MS + 20 * 7 + FLY_MS);
    expect(landAt(7, 8)).toBe(420);
    expect(totalMs(8)).toBeLessThanOrEqual(520);
  });

  it("ANIM-01 past eight the stagger compresses: thirty items finish within 560 ms [FR-G10]", () => {
    expect(stagger(30)).toBeCloseTo(140 / 29, 6);
    expect(totalMs(30)).toBeLessThanOrEqual(560);
    for (const n of [1, 2, 9, 12, 20, 50, 100]) {
      expect(totalMs(n), `${n} items`).toBeLessThanOrEqual(560);
      expect(stagger(n)).toBeLessThanOrEqual(20);
    }
    expect(totalMs(0)).toBe(0);
  });

  it("ANIM-01 landings spread at most 6 px and tilt at most 12 degrees, the same every time [FR-G10]", () => {
    for (let i = 0; i < 40; i++) {
      const s = scatter(i);
      expect(Math.abs(s.dx)).toBeLessThanOrEqual(6);
      expect(Math.abs(s.dy)).toBeLessThanOrEqual(6);
      expect(Math.abs(s.rotate)).toBeLessThanOrEqual(12);
      expect(scatter(i)).toEqual(s);
    }
  });
});
