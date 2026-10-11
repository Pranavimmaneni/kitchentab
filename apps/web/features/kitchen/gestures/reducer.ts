/**
 * The kitchen gesture engine (SPEC §2): a pure reducer. The pointer hook feeds it events with
 * timestamps and hit-test results; it answers with the next state and a list of effects for the
 * screen to run (open the sheet, move items, save an order, fire a haptic). No DOM, no timers.
 *
 * Hit zones (ADR-0033): while dragging, every visible item's cell has a leading edge, a centre and
 * a trailing edge. Edges move the gap (reorder); resting on a centre for COLLECT_DWELL_MS collects
 * the item. The centre is the hysteresis band that keeps the gap from flickering.
 */
import {
  COLLECT_DWELL_MS,
  DECIDE_PX,
  LONG_PRESS_MS,
  PRE_LIFT_SLOP_PX,
  STACK_MAX,
} from "./constants";
import {
  bubbleAt,
  distance,
  inCone,
  placeFan,
  type Fan,
  type Point,
  type Viewport,
} from "./geometry";

export type Loc = "freezer" | "fridge" | "pantry";

export type Phase =
  "IDLE" | "PRESSING" | "LIFTED" | "MENU_TRACKING" | "MENU_STATIC" | "DRAGGING" | "COLLECTING";

/** What is under the finger, as the hook measured it from the cached cell rects. */
export type Hit =
  | { kind: "item"; id: string; zone: "before" | "centre" | "after"; section: Loc }
  | { kind: "section"; section: Loc }
  | { kind: "bin"; to: Loc }
  | { kind: "none" };

export type HapticEvent = "lift" | "tick" | "collect" | "success" | "blocked";

export type Effect =
  | { type: "tap"; itemId: string }
  | { type: "haptic"; event: HapticEvent; reorder?: boolean }
  | { type: "choose"; itemId: string; index: 0 | 1 | 2 }
  | { type: "reorder"; section: Loc; order: string[] }
  | { type: "move"; itemIds: string[]; to: Loc }
  | { type: "blocked"; to: Loc }
  | { type: "home" };

export type GestureEvent =
  | {
      type: "down";
      itemId: string;
      pointerId: number;
      point: Point;
      time: number;
      /** Centre of the pressed item, viewport coordinates. */
      centre: Point;
      viewport: Viewport;
      selectMode: boolean;
      picked: readonly string[];
      /** Every item on screen and where it is now. */
      items: Readonly<Record<string, Loc>>;
      /** The pressed item's section, in its displayed order. */
      order: readonly string[];
    }
  | { type: "move"; pointerId: number; point: Point; time: number; hit: Hit }
  | { type: "up"; pointerId: number; point: Point; time: number; hit: Hit }
  | { type: "timer"; time: number }
  /** Second pointer, pointercancel, Escape, page hidden, scroll while a static menu is open. */
  | { type: "cancel" }
  /** A second finger landed. */
  | { type: "pointer"; pointerId: number }
  /** Right-click or Shift+F10 on an item: the fan as a real menu, no gesture. */
  | { type: "menu"; itemId: string; centre: Point; viewport: Viewport; time: number }
  | { type: "choose"; index: 0 | 1 | 2 }
  /** Live data changed under the gesture (SYNC-G2, SYNC-G6). Missing ids are gone. */
  | { type: "sync"; items: Readonly<Record<string, Loc>> };

export type GestureState = {
  phase: Phase;
  pointerId: number | null;
  itemId: string | null;
  /** Where the press started, and where the finger was when the item lifted. */
  start: Point;
  liftAt: Point;
  finger: Point;
  pressedAt: number;
  selectMode: boolean;
  picked: readonly string[];
  items: Readonly<Record<string, Loc>>;
  /** The pressed item's section and its order, with the dragged item at the gap. */
  section: Loc | null;
  original: readonly string[];
  order: readonly string[];
  fan: Fan | null;
  hoverBubble: 0 | 1 | 2 | null;
  /** Items in hand, pressed item first. */
  stack: readonly string[];
  /** Ids that left the stack because they're gone; the screen shows "Yogurt was used up". */
  dropped: readonly string[];
  dwell: { id: string; since: number } | null;
  overBin: Loc | null;
  /** A different section under the finger: dropping there moves, not reorders. */
  overSection: Loc | null;
  lastReorderTick: number;
};

export const initialGesture: GestureState = {
  phase: "IDLE",
  pointerId: null,
  itemId: null,
  start: { x: 0, y: 0 },
  liftAt: { x: 0, y: 0 },
  finger: { x: 0, y: 0 },
  pressedAt: 0,
  selectMode: false,
  picked: [],
  items: {},
  section: null,
  original: [],
  order: [],
  fan: null,
  hoverBubble: null,
  stack: [],
  dropped: [],
  dwell: null,
  overBin: null,
  overSection: null,
  lastReorderTick: -Infinity,
};

/** Reorder ticks are capped at one per 80 ms (SPEC §6). */
const REORDER_TICK_MS = 80;

export type Step = { state: GestureState; effects: Effect[] };

const idle = (effects: Effect[] = []): Step => ({ state: initialGesture, effects });

/** When the hook should next send a `timer` event, or null for none. */
export function nextDeadline(s: GestureState): number | null {
  if (s.phase === "PRESSING") return s.pressedAt + LONG_PRESS_MS;
  if ((s.phase === "DRAGGING" || s.phase === "COLLECTING") && s.dwell)
    return s.dwell.since + COLLECT_DWELL_MS;
  return null;
}

/** True from the lift until IDLE: the screen blocks page scroll with a non-passive touchmove. */
export function holdsPage(s: GestureState): boolean {
  return (
    s.phase === "LIFTED" ||
    s.phase === "MENU_TRACKING" ||
    s.phase === "DRAGGING" ||
    s.phase === "COLLECTING"
  );
}

/** Every dragged item is already at `to`: that bin shows HERE and refuses the drop. */
export function isHere(s: GestureState, to: Loc): boolean {
  return s.stack.length > 0 && s.stack.every((id) => s.items[id] === to);
}

function lift(s: GestureState): Step {
  const id = s.itemId as string;
  if (s.selectMode) {
    const picked = s.picked.includes(id);
    const stack = picked ? [id, ...s.picked.filter((p) => p !== id)].slice(0, STACK_MAX) : [id];
    return {
      state: {
        ...s,
        phase: picked ? "COLLECTING" : "DRAGGING",
        liftAt: s.finger,
        stack,
        order: picked ? s.original : s.order,
      },
      effects: [{ type: "haptic", event: "lift" }],
    };
  }
  return {
    state: { ...s, phase: "LIFTED", liftAt: s.finger, stack: [id] },
    effects: [{ type: "haptic", event: "lift" }],
  };
}

function withDwell(s: GestureState, hit: Hit, time: number): GestureState {
  if (
    hit.kind !== "item" ||
    hit.zone !== "centre" ||
    s.stack.includes(hit.id) ||
    s.stack.length >= STACK_MAX
  ) {
    return s.dwell ? { ...s, dwell: null } : s;
  }
  if (s.dwell?.id === hit.id) return s;
  return { ...s, dwell: { id: hit.id, since: time } };
}

function collectIfDue(s: GestureState, time: number): Step {
  if (!s.dwell || time - s.dwell.since < COLLECT_DWELL_MS || s.stack.length >= STACK_MAX)
    return { state: s, effects: [] };
  if (!(s.dwell.id in s.items)) return { state: { ...s, dwell: null }, effects: [] };
  return {
    // Collecting freezes the layout: the gap goes home and picked cells show dashed ghosts.
    state: {
      ...s,
      phase: "COLLECTING",
      stack: [...s.stack, s.dwell.id],
      dwell: null,
      order: s.original,
    },
    effects: [{ type: "haptic", event: "collect" }],
  };
}

/** Reorder: put the dragged item just before or after `target` in its own section. */
function placeGap(s: GestureState, target: string, zone: "before" | "after"): readonly string[] {
  const me = s.itemId as string;
  const rest = s.order.filter((id) => id !== me);
  const at = rest.indexOf(target);
  if (at < 0) return s.order;
  const i = zone === "before" ? at : at + 1;
  return [...rest.slice(0, i), me, ...rest.slice(i)];
}

const sameOrder = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

function track(s: GestureState, ev: { point: Point; time: number; hit: Hit }): Step {
  const effects: Effect[] = [];
  let next: GestureState = { ...s, finger: ev.point };
  const { hit } = ev;

  const bin = hit.kind === "bin" ? hit.to : null;
  if (bin !== s.overBin) {
    next.overBin = bin;
    if (bin) effects.push({ type: "haptic", event: "tick" });
  }
  const section = hit.kind === "item" || hit.kind === "section" ? hit.section : null;
  next.overSection = section && section !== s.section ? section : null;

  if (
    s.phase === "DRAGGING" &&
    hit.kind === "item" &&
    hit.section === s.section &&
    hit.zone !== "centre" &&
    hit.id !== s.itemId
  ) {
    const order = placeGap(next, hit.id, hit.zone);
    if (!sameOrder(order, next.order)) {
      next.order = order;
      if (ev.time - s.lastReorderTick >= REORDER_TICK_MS) {
        next.lastReorderTick = ev.time;
        effects.push({ type: "haptic", event: "tick", reorder: true });
      }
    }
  }
  next = withDwell(next, hit, ev.time);
  const due = collectIfDue(next, ev.time);
  return { state: due.state, effects: [...effects, ...due.effects] };
}

function drop(s: GestureState, hit: Hit): Step {
  // A stack only lands in a bin; a single drag may also land on the other section of the fridge.
  const to = hit.kind === "bin" ? hit.to : s.phase === "DRAGGING" ? s.overSection : null;
  if (to) {
    if (isHere(s, to))
      return idle([
        { type: "haptic", event: "blocked" },
        { type: "blocked", to },
        { type: "home" },
      ]);
    const itemIds = s.stack.filter((id) => s.items[id] !== to);
    return idle([{ type: "move", itemIds, to }]);
  }
  if (s.phase === "DRAGGING" && s.section && !sameOrder(s.order, s.original)) {
    return idle([{ type: "reorder", section: s.section, order: [...s.order] }]);
  }
  return idle([{ type: "home" }]);
}

export function reduce(s: GestureState, ev: GestureEvent): Step {
  switch (ev.type) {
    case "down": {
      if (s.phase === "MENU_STATIC") return idle();
      if (s.phase !== "IDLE") {
        // A second finger: cancel safely (SPEC §2 plumbing 5).
        return ev.pointerId === s.pointerId
          ? { state: s, effects: [] }
          : idle(s.phase === "PRESSING" ? [] : [{ type: "home" }]);
      }
      return {
        state: {
          ...initialGesture,
          phase: "PRESSING",
          pointerId: ev.pointerId,
          itemId: ev.itemId,
          start: ev.point,
          finger: ev.point,
          liftAt: ev.point,
          pressedAt: ev.time,
          selectMode: ev.selectMode,
          picked: ev.picked,
          items: ev.items,
          section: ev.items[ev.itemId] ?? null,
          original: ev.order,
          order: ev.order,
          fan: ev.selectMode ? null : placeFan(ev.centre, ev.viewport),
        },
        effects: [],
      };
    }

    case "pointer":
      if (s.phase === "IDLE" || s.phase === "MENU_STATIC" || ev.pointerId === s.pointerId)
        return { state: s, effects: [] };
      return idle(s.phase === "PRESSING" ? [] : [{ type: "home" }]);

    case "timer": {
      if (s.phase === "PRESSING" && ev.time - s.pressedAt >= LONG_PRESS_MS) return lift(s);
      if (s.phase === "DRAGGING" || s.phase === "COLLECTING") return collectIfDue(s, ev.time);
      return { state: s, effects: [] };
    }

    case "move": {
      if (ev.pointerId !== s.pointerId) return { state: s, effects: [] };
      switch (s.phase) {
        case "PRESSING":
          // Moving before the lift is a scroll: let the page have it.
          if (distance(ev.point, s.start) > PRE_LIFT_SLOP_PX) return idle();
          return { state: { ...s, finger: ev.point }, effects: [] };
        case "LIFTED": {
          if (distance(ev.point, s.liftAt) < DECIDE_PX)
            return { state: { ...s, finger: ev.point }, effects: [] };
          if (s.fan && inCone(s.fan, ev.point)) {
            return trackMenu({ ...s, phase: "MENU_TRACKING" }, ev.point);
          }
          // Drag wins: the fan folds away (MENU_FOLD_MS) and the item follows the finger.
          return track({ ...s, phase: "DRAGGING", hoverBubble: null }, ev);
        }
        case "MENU_TRACKING":
          return trackMenu(s, ev.point);
        case "DRAGGING":
        case "COLLECTING":
          return track(s, ev);
        default:
          return { state: s, effects: [] };
      }
    }

    case "up": {
      if (ev.pointerId !== s.pointerId) return { state: s, effects: [] };
      switch (s.phase) {
        case "PRESSING":
          return idle([{ type: "tap", itemId: s.itemId as string }]);
        case "LIFTED":
          if (s.selectMode || !s.fan) return idle([{ type: "home" }]);
          // A hold without travel leaves the bubbles open as ordinary buttons.
          return {
            state: { ...s, phase: "MENU_STATIC", pointerId: null, hoverBubble: null },
            effects: [],
          };
        case "MENU_TRACKING": {
          const index = s.fan ? bubbleAt(s.fan, ev.point) : null;
          return idle(
            index === null
              ? [{ type: "home" }]
              : [{ type: "choose", itemId: s.itemId as string, index }],
          );
        }
        case "DRAGGING":
        case "COLLECTING": {
          const moved = track(s, ev).state;
          return drop(moved, ev.hit);
        }
        default:
          return { state: s, effects: [] };
      }
    }

    case "menu":
      if (s.phase !== "IDLE" && s.phase !== "MENU_STATIC") return { state: s, effects: [] };
      return {
        state: {
          ...initialGesture,
          phase: "MENU_STATIC",
          itemId: ev.itemId,
          start: ev.centre,
          liftAt: ev.centre,
          finger: ev.centre,
          pressedAt: ev.time,
          stack: [ev.itemId],
          fan: placeFan(ev.centre, ev.viewport),
        },
        effects: [],
      };

    case "choose":
      if (s.phase !== "MENU_STATIC") return { state: s, effects: [] };
      return idle([{ type: "choose", itemId: s.itemId as string, index: ev.index }]);

    case "cancel":
      if (s.phase === "IDLE") return { state: s, effects: [] };
      return idle(s.phase === "PRESSING" || s.phase === "MENU_STATIC" ? [] : [{ type: "home" }]);

    case "sync": {
      if (s.phase === "IDLE") return { state: s, effects: [] };
      const live = (id: string) => id in ev.items;
      if (
        s.itemId &&
        !live(s.itemId) &&
        (s.phase === "PRESSING" || s.phase === "LIFTED" || s.phase.startsWith("MENU"))
      ) {
        return idle([{ type: "home" }]);
      }
      const stack = s.stack.filter(live);
      const gone = s.stack.filter((id) => !live(id));
      if ((s.phase === "DRAGGING" || s.phase === "COLLECTING") && stack.length === 0)
        return idle([{ type: "home" }]);
      // New items join the end of the section; the dragged item stays where it is (SYNC-G6).
      const section = s.section;
      const added = section
        ? Object.keys(ev.items).filter((id) => ev.items[id] === section && !s.original.includes(id))
        : [];
      const keep = (list: readonly string[]) => [
        ...list.filter((id) => live(id) || id === s.itemId),
        ...added,
      ];
      return {
        state: {
          ...s,
          items: ev.items,
          stack,
          dropped: [...s.dropped, ...gone],
          original: keep(s.original),
          order: keep(s.order),
          dwell: s.dwell && live(s.dwell.id) ? s.dwell : null,
        },
        effects: [],
      };
    }
  }
}

function trackMenu(s: GestureState, point: Point): Step {
  const index = s.fan ? bubbleAt(s.fan, point) : null;
  const effects: Effect[] =
    index !== null && index !== s.hoverBubble ? [{ type: "haptic", event: "tick" }] : [];
  return { state: { ...s, finger: point, hoverBubble: index }, effects };
}

/**
 * Personal order with a filter on: the screen only reordered the visible items. Keep the hidden
 * ones where they were and lay the visible ones into the visible positions in their new order.
 */
export function mergeOrder(full: readonly string[], visibleNew: readonly string[]): string[] {
  const visible = new Set(visibleNew);
  const queue = [...visibleNew];
  const out = full.map((id) => (visible.has(id) ? (queue.shift() as string) : id));
  // Visible ids the full list didn't have yet go at the end.
  return [...out, ...queue];
}
