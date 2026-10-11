/**
 * Haptics (docs/features/kitchen-gestures/SPEC.md §6, FR-G09): one `haptic(event)` for the whole
 * app. Android: `navigator.vibrate`. iPhone: Safari has no Vibration API, so a hidden
 * `<input type="checkbox" switch>` is toggled through its label, which gives one system tick per
 * click during a user gesture (best effort; isolated in `iosTick` so it can go if Safari changes).
 * Desktop: nothing. Every haptic has a visual twin, so a missing one never loses information.
 */

export type HapticEvent = "lift" | "tick" | "collect" | "success" | "blocked";

/** Android patterns (ms on / off / on). */
export const VIBRATE: Record<HapticEvent, number | number[]> = {
  lift: 18,
  tick: 6,
  collect: 10,
  success: [12, 60, 24],
  blocked: [24, 40, 24],
};
/** The reorder slot tick is a touch lighter than the bubble/bin tick. */
export const REORDER_TICK = 5;

/** iPhone: when each tick fires (ms after the first). */
export const IOS_TICKS: Record<HapticEvent, number[]> = {
  lift: [0],
  tick: [0],
  collect: [0],
  success: [0, 60],
  blocked: [0, 40],
};

/** At least this long between two haptics (SPEC §6 global rules). */
export const HAPTIC_GAP_MS = 60;

const STORAGE_KEY = "kt.haptics";

export type HapticSupport = "vibrate" | "ios-switch" | null;

/** Phones only: a coarse pointer and a way to buzz. */
export function hapticSupport(): HapticSupport {
  if (typeof window === "undefined" || typeof navigator === "undefined") return null;
  const touch =
    (typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches) ||
    navigator.maxTouchPoints > 1;
  if (!touch) return null;
  if (typeof navigator.vibrate === "function") return "vibrate";
  if (typeof HTMLInputElement !== "undefined" && "switch" in HTMLInputElement.prototype) {
    return "ios-switch";
  }
  return null;
}

/** Settings › Kitchen › Haptics (default on). */
export function hapticsEnabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setHapticsEnabled(on: boolean): void {
  try {
    if (on) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, "off");
  } catch {
    // remembered for this visit only
  }
}

let lastAt = -Infinity;
let iosLabel: HTMLLabelElement | null = null;

function iosTick(): void {
  if (!iosLabel || !iosLabel.isConnected) {
    const label = document.createElement("label");
    label.setAttribute("aria-hidden", "true");
    label.dataset.ktHaptic = "";
    label.style.cssText =
      "position:fixed;left:0;top:0;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none;clip-path:inset(50%)";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.setAttribute("switch", "");
    input.tabIndex = -1;
    label.append(input);
    document.body.append(label);
    iosLabel = label;
  }
  iosLabel.click();
}

/**
 * Fire one haptic for the person's own gesture. Returns whether it fired. Never call it for other
 * people's live changes or on cancel (HAPT-03).
 */
export function haptic(event: HapticEvent, opts: { reorder?: boolean } = {}): boolean {
  const support = hapticSupport();
  if (!support || !hapticsEnabled()) return false;
  const now = typeof performance !== "undefined" ? performance.now() : Date.now();
  if (now - lastAt < HAPTIC_GAP_MS) return false;
  lastAt = now;
  if (support === "vibrate") {
    // Chrome refuses (and logs) a vibration before the page's first tap; a touch only counts on
    // release, so the very first long-press on a fresh page stays silent.
    const activation = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } })
      .userActivation;
    if (activation && !activation.hasBeenActive) return false;
    const pattern = event === "tick" && opts.reorder ? REORDER_TICK : VIBRATE[event];
    try {
      navigator.vibrate(pattern);
    } catch {
      return false;
    }
    return true;
  }
  for (const at of IOS_TICKS[event]) {
    if (at === 0) iosTick();
    else window.setTimeout(iosTick, at);
  }
  return true;
}

/** Tests only: forget the throttle and the hidden switch. */
export function resetHaptics(): void {
  lastAt = -Infinity;
  iosLabel?.remove();
  iosLabel = null;
}
