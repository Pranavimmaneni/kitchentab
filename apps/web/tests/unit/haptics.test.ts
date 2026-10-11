import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { initialGesture, reduce, type GestureEvent } from "@/features/kitchen/gestures/reducer";
import {
  HAPTIC_GAP_MS,
  REORDER_TICK,
  VIBRATE,
  haptic,
  hapticSupport,
  hapticsEnabled,
  resetHaptics,
  setHapticsEnabled,
  type HapticEvent,
} from "@/lib/haptics";

const EVENTS: HapticEvent[] = ["lift", "tick", "collect", "success", "blocked"];

function phone({ vibrate, iosSwitch }: { vibrate: boolean; iosSwitch: boolean }) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(pointer: coarse)", media: q }));
  const vib = vi.fn(() => true);
  if (vibrate) Object.defineProperty(navigator, "vibrate", { value: vib, configurable: true });
  if (iosSwitch)
    Object.defineProperty(HTMLInputElement.prototype, "switch", {
      value: false,
      configurable: true,
    });
  return vib;
}

let now = 1000;
beforeEach(() => {
  now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  resetHaptics();
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete (navigator as { vibrate?: unknown }).vibrate;
  delete (HTMLInputElement.prototype as { switch?: unknown }).switch;
  resetHaptics();
});

describe("haptics (SPEC §6)", () => {
  it("HAPT-01 every event maps to its Android pattern; the reorder tick is lighter [FR-G09]", () => {
    const vib = phone({ vibrate: true, iosSwitch: false });
    expect(hapticSupport()).toBe("vibrate");
    for (const ev of EVENTS) {
      now += HAPTIC_GAP_MS;
      expect(haptic(ev)).toBe(true);
      expect(vib).toHaveBeenLastCalledWith(VIBRATE[ev]);
    }
    now += HAPTIC_GAP_MS;
    haptic("tick", { reorder: true });
    expect(vib).toHaveBeenLastCalledWith(REORDER_TICK);
    expect(VIBRATE).toEqual({
      lift: 18,
      tick: 6,
      collect: 10,
      success: [12, 60, 24],
      blocked: [24, 40, 24],
    });
  });

  it("HAPT-01 at least 60 ms between haptics [FR-G09]", () => {
    const vib = phone({ vibrate: true, iosSwitch: false });
    expect(haptic("tick")).toBe(true);
    now += HAPTIC_GAP_MS - 1;
    expect(haptic("tick")).toBe(false);
    now += 1;
    expect(haptic("tick")).toBe(true);
    expect(vib).toHaveBeenCalledTimes(2);
  });

  it("HAPT-01 the Settings switch turns them off and on (default on) [FR-G09]", () => {
    const vib = phone({ vibrate: true, iosSwitch: false });
    expect(hapticsEnabled()).toBe(true);
    setHapticsEnabled(false);
    expect(haptic("lift")).toBe(false);
    expect(vib).not.toHaveBeenCalled();
    setHapticsEnabled(true);
    expect(haptic("lift")).toBe(true);
  });

  it("HAPT-02 iPhone: the hidden switch's label is clicked once per tick of the pattern [FR-G09]", () => {
    vi.useFakeTimers({ toFake: ["setTimeout"] });
    phone({ vibrate: false, iosSwitch: true });
    expect(hapticSupport()).toBe("ios-switch");
    const click = vi.spyOn(HTMLLabelElement.prototype, "click");
    haptic("lift");
    expect(click).toHaveBeenCalledTimes(1);
    const label = document.querySelector<HTMLLabelElement>("[data-kt-haptic]");
    expect(label?.getAttribute("aria-hidden")).toBe("true");
    expect(label?.querySelector("input")?.hasAttribute("switch")).toBe(true);
    now += HAPTIC_GAP_MS;
    haptic("success");
    expect(click).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(60);
    expect(click).toHaveBeenCalledTimes(3);
    expect(document.querySelectorAll("[data-kt-haptic]")).toHaveLength(1);
  });

  it("HAPT-02 no-op where the device can't (desktop, or no API): the setting hides [FR-G09]", () => {
    expect(hapticSupport()).toBeNull(); // jsdom: no coarse pointer
    expect(haptic("lift")).toBe(false);
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(pointer: coarse)", media: q }));
    expect(hapticSupport()).toBeNull(); // a touch screen with neither API
  });

  it("HAPT-03 no haptic for other people's live changes or for a cancel [FR-G09]", () => {
    const items = { a: "fridge", b: "fridge" } as const;
    const down: GestureEvent = {
      type: "down",
      itemId: "a",
      pointerId: 1,
      point: { x: 200, y: 400 },
      time: 0,
      centre: { x: 200, y: 400 },
      viewport: { width: 390, height: 844 },
      selectMode: false,
      picked: [],
      items,
      order: ["a", "b"],
    };
    let s = reduce(initialGesture, down).state;
    s = reduce(s, { type: "timer", time: 350 }).state;
    s = reduce(s, {
      type: "move",
      pointerId: 1,
      point: { x: 240, y: 400 },
      time: 400,
      hit: { kind: "none" },
    }).state;
    const remote = reduce(s, { type: "sync", items: { ...items, c: "fridge" } });
    expect(remote.effects).toEqual([]);
    for (const cancel of [
      { type: "cancel" },
      { type: "pointer", pointerId: 2 },
    ] as GestureEvent[]) {
      expect(reduce(remote.state, cancel).effects.some((e) => e.type === "haptic")).toBe(false);
    }
  });
});
