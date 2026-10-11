/**
 * Stocking animation timing (docs/features/kitchen-gestures/SPEC.md §7, render sort-storyboard):
 * lift 0–60 ms, then item i flies from 60 + stagger·i for 220 ms, lands and fades over 60 ms.
 * Eight items take ~480 ms; past eight the stagger compresses so the whole thing stays ≤ ~520 ms.
 */

export const LIFT_MS = 60;
export const DIM_MS = 120;
export const FLY_MS = 220;
export const SQUASH_MS = 100;
export const BADGE_MS = 90;
/** Icons fade over 60 ms from the moment they land. */
export const FADE_MS = 60;
export const FLY_BACK_MS = 200;

export const EASE_LIFT = "cubic-bezier(.2,1.4,.4,1)";
/** Two nested elements so the path arcs: X eases out, Y eases in. */
export const EASE_X = "cubic-bezier(.25,.1,.35,1)";
export const EASE_Y = "cubic-bezier(.55,0,.85,.4)";

export function stagger(n: number): number {
  return n <= 8 ? 20 : Math.min(20, 140 / (n - 1));
}

export function flyStart(i: number, n: number): number {
  return LIFT_MS + stagger(n) * i;
}

export function landAt(i: number, n: number): number {
  return flyStart(i, n) + FLY_MS;
}

/** When the last icon has landed and faded: the animation is over. */
export function totalMs(n: number): number {
  if (n <= 0) return 0;
  return landAt(n - 1, n) + FADE_MS;
}

/** Small, stable landing spread (±6 px) and tilt (±12°) per item, so a bin looks filled by hand. */
export function scatter(i: number): { dx: number; dy: number; rotate: number } {
  const s = Math.sin((i + 1) * 12.9898) * 43758.5453;
  const r = s - Math.floor(s);
  const t = Math.cos((i + 1) * 78.233) * 12345.6789;
  const q = t - Math.floor(t);
  return {
    dx: Math.round((r * 2 - 1) * 6),
    dy: Math.round((q * 2 - 1) * 6),
    rotate: Math.round((r - q) * 12),
  };
}
