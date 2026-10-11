/**
 * Fan placement and the cone test (SPEC §2). Angles in degrees, 0° = right, y grows downward, so
 * −90° is straight up. Pure functions: the reducer and the overlay both use them.
 */
import {
  BUBBLE_HOVER_PX,
  FLIP_BELOW_TOP_PX,
  MENU_FAN_DEG,
  MENU_RADIUS_PX,
  SIDE_EDGE_PX,
  SIDE_ROTATE_DEG,
} from "./constants";

export type Point = { x: number; y: number };
export type Viewport = { width: number; height: number };

export type Fan = {
  origin: Point;
  /** Bubble angles, left to right as the person sees them. */
  angles: [number, number, number];
  /** Bubble centres. */
  centres: [Point, Point, Point];
  /** The cone's middle direction; the cone is MENU_FAN_DEG wide around it. */
  axis: number;
};

const rad = (deg: number) => (deg * Math.PI) / 180;

export function polar(origin: Point, deg: number, radius = MENU_RADIUS_PX): Point {
  return { x: origin.x + radius * Math.cos(rad(deg)), y: origin.y + radius * Math.sin(rad(deg)) };
}

/** Signed smallest difference a − b in degrees, in (−180, 180]. */
export function angleDiff(a: number, b: number): number {
  let d = (((a - b) % 360) + 360) % 360;
  if (d > 180) d -= 360;
  return d;
}

export function angleTo(from: Point, to: Point): number {
  return (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI;
}

/**
 * Where the three bubbles go for an item centred at `origin`: above by default (−150°, −90°,
 * −30°); flipped below near the top of the viewport (+150°, +90°, +30° mirrored to read left to
 * right); rotated 30° inward within SIDE_EDGE_PX of a side. If a bubble would still poke out,
 * rotate further inward in 15° steps until all three fit, and clamp them on screen if nothing fits.
 */
export function placeFan(origin: Point, viewport: Viewport): Fan {
  const below = origin.y < FLIP_BELOW_TOP_PX;
  let axis = below ? 90 : -90;
  // Inward = towards the screen centre: rotating the axis away from the near edge.
  const nearLeft = origin.x < SIDE_EDGE_PX;
  const nearRight = origin.x > viewport.width - SIDE_EDGE_PX;
  const inward = (deg: number) => {
    if (nearLeft) return below ? -deg : deg; // axis −90 → −60 (towards the right)
    if (nearRight) return below ? deg : -deg; // axis −90 → −120 (towards the left)
    return 0;
  };
  axis += inward(SIDE_ROTATE_DEG);
  const make = (ax: number, radius = MENU_RADIUS_PX): Fan => {
    const half = MENU_FAN_DEG / 2;
    // Left-to-right reading order on screen.
    const raw: [number, number, number] = below
      ? [ax + half, ax, ax - half]
      : [ax - half, ax, ax + half];
    const centres = raw.map((a) => polar(origin, a, radius)) as [Point, Point, Point];
    return { origin, angles: raw, centres, axis: ax };
  };
  let fan = make(axis);
  const fits = (f: Fan) =>
    f.centres.every(
      (c) =>
        c.x - BUBBLE_HOVER_PX / 2 >= 0 &&
        c.x + BUBBLE_HOVER_PX / 2 <= viewport.width &&
        c.y - BUBBLE_HOVER_PX / 2 >= 0 &&
        c.y + BUBBLE_HOVER_PX / 2 <= viewport.height,
    );
  // Nudge further inward if the edge still clips a bubble (narrow phones, corner items).
  for (let step = 1; step <= 4 && !fits(fan); step++) {
    const extra = nearLeft || nearRight ? inward(15 * step) : 0;
    if (extra === 0) break;
    fan = make(axis + extra);
  }
  if (fits(fan)) return fan;
  // Corner items on narrow phones: no rotation fits a 120° fan. Point it where it overflows least,
  // then pull each bubble inside.
  const r = BUBBLE_HOVER_PX / 2;
  const overflow = (f: Fan) =>
    f.centres.reduce(
      (sum, c) =>
        sum +
        Math.max(0, r - c.x) +
        Math.max(0, c.x + r - viewport.width) +
        Math.max(0, r - c.y) +
        Math.max(0, c.y + r - viewport.height),
      0,
    );
  // A slightly longer reach clears the corner without squeezing the bubbles together.
  for (const radius of [MENU_RADIUS_PX, MENU_RADIUS_PX + 12, MENU_RADIUS_PX + 24]) {
    for (let ax = -180; ax < 180; ax += 5) {
      const candidate = make(ax, radius);
      if (overflow(candidate) < overflow(fan)) fan = candidate;
    }
    if (fits(fan)) return fan;
  }
  const clamp = (v: number, max: number) => Math.min(Math.max(v, r), max - r);
  const centres = fan.centres.map((c) => ({ x: clamp(c.x, viewport.width), y: clamp(c.y, viewport.height) }));
  return { ...fan, centres: centres as [Point, Point, Point] };
}

/** Is the finger inside the fan's cone (MENU_FAN_DEG wide around the axis)? */
export function inCone(fan: Fan, finger: Point): boolean {
  return Math.abs(angleDiff(angleTo(fan.origin, finger), fan.axis)) <= MENU_FAN_DEG / 2;
}

/** The bubble under the finger, if any (hit area = the hovered size). */
export function bubbleAt(fan: Fan, finger: Point): 0 | 1 | 2 | null {
  for (const i of [0, 1, 2] as const) {
    const c = fan.centres[i];
    if (Math.hypot(finger.x - c.x, finger.y - c.y) <= BUBBLE_HOVER_PX / 2) return i;
  }
  return null;
}

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}
