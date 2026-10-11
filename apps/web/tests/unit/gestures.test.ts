import { describe, expect, it } from "vitest";

import {
  BUBBLE_HOVER_PX,
  BUBBLE_PX,
  COLLECT_DWELL_MS,
  LONG_PRESS_MS,
  MENU_RADIUS_PX,
  STACK_MAX,
} from "@/features/kitchen/gestures/constants";
import { bubbleAt, inCone, placeFan } from "@/features/kitchen/gestures/geometry";
import {
  initialGesture,
  isHere,
  mergeOrder,
  nextDeadline,
  reduce,
  type Effect,
  type GestureEvent,
  type GestureState,
  type Hit,
  type Loc,
} from "@/features/kitchen/gestures/reducer";

const VIEW = { width: 390, height: 844 };
const ITEMS: Record<string, Loc> = {
  milk: "fridge",
  eggs: "fridge",
  yogurt: "fridge",
  kimchi: "fridge",
  tofu: "fridge",
  dumplings: "freezer",
  rice: "pantry",
};
const FRIDGE = ["milk", "eggs", "yogurt", "kimchi", "tofu"];
const CENTRE = { x: 200, y: 400 };
const NONE: Hit = { kind: "none" };

type Run = { state: GestureState; effects: Effect[] };

function run(events: GestureEvent[], from: GestureState = initialGesture): Run {
  let state = from;
  const effects: Effect[] = [];
  for (const ev of events) {
    const step = reduce(state, ev);
    state = step.state;
    effects.push(...step.effects);
  }
  return { state, effects };
}

const down = (over: Partial<Extract<GestureEvent, { type: "down" }>> = {}): GestureEvent => ({
  type: "down",
  itemId: "eggs",
  pointerId: 1,
  point: CENTRE,
  time: 0,
  centre: CENTRE,
  viewport: VIEW,
  selectMode: false,
  picked: [],
  items: ITEMS,
  order: FRIDGE,
  ...over,
});
const move = (x: number, y: number, time: number, hit: Hit = NONE): GestureEvent => ({
  type: "move",
  pointerId: 1,
  point: { x, y },
  time,
  hit,
});
const up = (x: number, y: number, time: number, hit: Hit = NONE): GestureEvent => ({
  type: "up",
  pointerId: 1,
  point: { x, y },
  time,
  hit,
});
const timer = (time: number): GestureEvent => ({ type: "timer", time });
const lifted = (over: Partial<Extract<GestureEvent, { type: "down" }>> = {}) =>
  run([down(over), timer(LONG_PRESS_MS)]);
/** Lift, then leave the cone sideways: a drag. */
const dragging = (over: Partial<Extract<GestureEvent, { type: "down" }>> = {}) =>
  run([move(CENTRE.x + 30, CENTRE.y + 5, LONG_PRESS_MS + 10)], lifted(over).state);
const item = (
  id: string,
  zone: "before" | "centre" | "after" = "centre",
  section: Loc = ITEMS[id] as Loc,
): Hit => ({
  kind: "item",
  id,
  zone,
  section,
});
const without = (items: Record<string, Loc>, id: string) =>
  Object.fromEntries(Object.entries(items).filter(([k]) => k !== id)) as Record<string, Loc>;
const types = (effects: Effect[]) =>
  effects.map((e) => (e.type === "haptic" ? `haptic:${e.event}` : e.type));

describe("press and lift (FR-G01)", () => {
  it("GEST-01 a 350 ms hold lifts the item with a lift haptic [FR-G01]", () => {
    const pressing = run([down()]);
    expect(pressing.state.phase).toBe("PRESSING");
    expect(nextDeadline(pressing.state)).toBe(LONG_PRESS_MS);
    expect(run([timer(LONG_PRESS_MS - 1)], pressing.state).state.phase).toBe("PRESSING");
    const after = run([timer(LONG_PRESS_MS)], pressing.state);
    expect(after.state.phase).toBe("LIFTED");
    expect(types(after.effects)).toEqual(["haptic:lift"]);
  });

  it("GEST-01 moving 9 px before the lift gives the page its scroll [FR-G01]", () => {
    const r = run([down(), move(CENTRE.x, CENTRE.y + 9, 100), timer(LONG_PRESS_MS)]);
    expect(r.state.phase).toBe("IDLE");
    expect(r.effects).toEqual([]);
  });

  it("GEST-01 small jitter under 8 px still lifts [FR-G01]", () => {
    const r = run([down(), move(CENTRE.x + 5, CENTRE.y + 5, 100), timer(LONG_PRESS_MS)]);
    expect(r.state.phase).toBe("LIFTED");
  });

  it("GEST-01 releasing before 350 ms is a tap that opens the item [FR-G01]", () => {
    const r = run([down(), up(CENTRE.x, CENTRE.y, 200)]);
    expect(r.state.phase).toBe("IDLE");
    expect(r.effects).toEqual([{ type: "tap", itemId: "eggs" }]);
  });
});

describe("menu or drag (FR-G03)", () => {
  it("GEST-02 12 px into the fan's cone tracks the menu; release on a bubble chooses it [FR-G03] [FR-G05]", () => {
    const { state } = lifted();
    const fan = state.fan!;
    const tracking = run([move(CENTRE.x, CENTRE.y - 12, 400)], state);
    expect(tracking.state.phase).toBe("MENU_TRACKING");
    const onBubble = run([move(fan.centres[2].x, fan.centres[2].y, 450)], tracking.state);
    expect(onBubble.state.hoverBubble).toBe(2);
    expect(types(onBubble.effects)).toEqual(["haptic:tick"]);
    const chosen = run([up(fan.centres[2].x, fan.centres[2].y, 500)], onBubble.state);
    expect(chosen.state.phase).toBe("IDLE");
    expect(chosen.effects).toEqual([{ type: "choose", itemId: "eggs", index: 2 }]);
  });

  it("GEST-02 releasing in the cone but off the bubbles chooses nothing [FR-G03] [FR-G05]", () => {
    const r = run(
      [move(CENTRE.x, CENTRE.y - 20, 400), up(CENTRE.x, CENTRE.y - 20, 420)],
      lifted().state,
    );
    expect(r.state.phase).toBe("IDLE");
    expect(r.effects.some((e) => e.type === "choose")).toBe(false);
  });

  it("GEST-02 12 px outside the cone is a drag and the fan folds [FR-G03] [FR-G05]", () => {
    const r = run([move(CENTRE.x + 12, CENTRE.y, 400)], lifted().state);
    expect(r.state.phase).toBe("DRAGGING");
    expect(r.state.hoverBubble).toBeNull();
  });

  it("GEST-02 under 12 px of travel then release leaves the bubbles open as buttons [FR-G03] [FR-G05]", () => {
    const r = run(
      [move(CENTRE.x + 4, CENTRE.y - 4, 400), up(CENTRE.x + 4, CENTRE.y - 4, 450)],
      lifted().state,
    );
    expect(r.state.phase).toBe("MENU_STATIC");
    expect(r.effects).toEqual([]);
    const chosen = run([{ type: "choose", index: 0 }], r.state);
    expect(chosen.effects).toEqual([{ type: "choose", itemId: "eggs", index: 0 }]);
  });

  it("GEST-02 right-click or Shift+F10 opens the static menu without a gesture [FR-G03] [FR-G05]", () => {
    const r = run([{ type: "menu", itemId: "milk", centre: CENTRE, viewport: VIEW, time: 0 }]);
    expect(r.state.phase).toBe("MENU_STATIC");
    expect(run([{ type: "cancel" }], r.state).state.phase).toBe("IDLE");
  });

  it("GEST-02 select mode never opens the fan [FR-G03] [FR-G05]", () => {
    const r = lifted({ selectMode: true });
    expect(r.state.fan).toBeNull();
    expect(r.state.phase).toBe("DRAGGING");
  });
});

describe("fan placement", () => {
  const onScreen = (fan: ReturnType<typeof placeFan>, width: number, height: number) =>
    fan.centres.every(
      (c) =>
        c.x - BUBBLE_HOVER_PX / 2 >= 0 &&
        c.x + BUBBLE_HOVER_PX / 2 <= width &&
        c.y - BUBBLE_HOVER_PX / 2 >= 0 &&
        c.y + BUBBLE_HOVER_PX / 2 <= height,
    );

  it("GEST-03 bubbles fan above at -150, -90, -30 degrees by default [FR-G03]", () => {
    const fan = placeFan(CENTRE, VIEW);
    expect(fan.angles).toEqual([-150, -90, -30]);
    expect(fan.centres.every((c) => c.y < CENTRE.y)).toBe(true);
    expect(fan.centres[0].x).toBeLessThan(fan.centres[2].x);
  });

  it("GEST-03 the fan flips below near the top of the viewport [FR-G03]", () => {
    const fan = placeFan({ x: 200, y: 120 }, VIEW);
    expect(fan.centres.every((c) => c.y > 120)).toBe(true);
    expect(fan.centres[0].x).toBeLessThan(fan.centres[2].x);
    expect(inCone(fan, { x: 200, y: 150 })).toBe(true);
    expect(inCone(fan, { x: 200, y: 90 })).toBe(false);
  });

  it("GEST-03 a first-column tile on a 375 px phone turns the fan inward instead of squeezing it [FR-G03]", () => {
    const origin = { x: 68, y: 356 };
    const fan = placeFan(origin, { width: 375, height: 812 });
    for (const c of fan.centres) {
      expect(Math.hypot(c.x - origin.x, c.y - origin.y)).toBeCloseTo(MENU_RADIUS_PX, 5);
    }
    expect(fan.axis).toBeGreaterThan(-90);
  });

  it("GEST-03 the fan rotates 30 degrees inward near the sides [FR-G03]", () => {
    expect(placeFan({ x: 40, y: 400 }, VIEW).axis).toBeGreaterThan(-90);
    expect(placeFan({ x: 350, y: 400 }, VIEW).axis).toBeLessThan(-90);
  });

  it("GEST-03 every bubble stays on screen at 320 px wide, corners included [FR-G03]", () => {
    const narrow = { width: 320, height: 640 };
    for (let x = 24; x <= 296; x += 8) {
      for (let y = 40; y <= 620; y += 10) {
        expect(onScreen(placeFan({ x, y }, narrow), narrow.width, narrow.height), `${x},${y}`).toBe(
          true,
        );
      }
    }
  });

  it("GEST-03 bubbles never overlap where a tile can be (16 px gutter, 64 px tiles, above the bottom bar) [FR-G03]", () => {
    const narrow = { width: 320, height: 640 };
    for (let x = 48; x <= 272; x += 8) {
      for (let y = 40; y <= 600; y += 10) {
        const [a, b, c] = placeFan({ x, y }, narrow).centres;
        const gap = Math.min(Math.hypot(a.x - b.x, a.y - b.y), Math.hypot(b.x - c.x, b.y - c.y));
        expect(gap, `${x},${y}`).toBeGreaterThanOrEqual(BUBBLE_PX);
      }
    }
  });

  it("GEST-03 bubble hit areas are the hovered size [FR-G03]", () => {
    const fan = placeFan(CENTRE, VIEW);
    const c = fan.centres[1];
    expect(bubbleAt(fan, { x: c.x + BUBBLE_HOVER_PX / 2 - 1, y: c.y })).toBe(1);
    expect(bubbleAt(fan, { x: c.x + BUBBLE_HOVER_PX / 2 + 1, y: c.y })).toBeNull();
  });
});

describe("reorder and collect (FR-G02, FR-G06, FR-G07)", () => {
  it("GEST-04 crossing a neighbour's edge moves the gap and commits the order on release [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    const r = run(
      [move(300, 400, 500, item("yogurt", "after")), up(300, 400, 520, item("yogurt", "after"))],
      d.state,
    );
    expect(r.effects).toContainEqual({
      type: "reorder",
      section: "fridge",
      order: ["milk", "yogurt", "eggs", "kimchi", "tofu"],
    });
    expect(r.effects).toContainEqual({ type: "haptic", event: "tick", reorder: true });
  });

  it("GEST-04 releasing without changing the order glides home and saves nothing [FR-G02] [FR-G06] [FR-G07]", () => {
    const r = run([up(CENTRE.x + 30, CENTRE.y, 500)], dragging().state);
    expect(r.effects).toEqual([{ type: "home" }]);
  });

  it("GEST-04 reorder ticks are capped at one per 80 ms [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    const r = run(
      [
        move(300, 400, 500, item("yogurt", "after")),
        move(350, 400, 540, item("kimchi", "after")),
        move(380, 400, 600, item("tofu", "after")),
      ],
      d.state,
    );
    expect(r.effects.filter((e) => e.type === "haptic")).toHaveLength(2);
    expect(r.state.order).toEqual(["milk", "yogurt", "kimchi", "tofu", "eggs"]);
  });

  it("GEST-04 resting 250 ms on another item collects it; 200 ms doesn't [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    const resting = run([move(300, 400, 500, item("kimchi"))], d.state);
    expect(nextDeadline(resting.state)).toBe(500 + COLLECT_DWELL_MS);
    const short = run([timer(700)], resting.state);
    expect(short.state.stack).toEqual(["eggs"]);
    const long = run([timer(750)], resting.state);
    expect(long.state.phase).toBe("COLLECTING");
    expect(long.state.stack).toEqual(["eggs", "kimchi"]);
    expect(types(long.effects)).toEqual(["haptic:collect"]);
  });

  it("GEST-04 leaving the item before the dwell ends restarts it [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    const r = run(
      [move(300, 400, 500, item("kimchi")), move(320, 400, 600, item("tofu")), timer(760)],
      d.state,
    );
    expect(r.state.stack).toEqual(["eggs"]);
    expect(run([timer(850)], r.state).state.stack).toEqual(["eggs", "tofu"]);
  });

  it("GEST-04 collecting freezes the reorder: the gap goes home and edges no longer shift [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    const moved = run([move(300, 400, 500, item("yogurt", "after"))], d.state);
    expect(moved.state.order).not.toEqual(FRIDGE);
    const collecting = run([move(320, 400, 600, item("kimchi")), timer(850)], moved.state);
    expect(collecting.state.phase).toBe("COLLECTING");
    expect(collecting.state.order).toEqual(FRIDGE);
    const edge = run([move(340, 400, 900, item("tofu", "after"))], collecting.state);
    expect(edge.state.order).toEqual(FRIDGE);
    // Releasing in the list sends the stack home with no write.
    expect(run([up(340, 400, 950, item("tofu", "after"))], edge.state).effects).toEqual([
      { type: "home" },
    ]);
  });

  it(`GEST-04 a stack holds at most ${STACK_MAX} items [FR-G02] [FR-G06] [FR-G07]`, () => {
    const many: Record<string, Loc> = {};
    const ids = Array.from({ length: 25 }, (_, i) => `i${i}`);
    for (const id of ids) many[id] = "pantry";
    let s = run(
      [move(CENTRE.x + 30, CENTRE.y, 360)],
      run([down({ itemId: "i0", items: many, order: ids }), timer(LONG_PRESS_MS)]).state,
    ).state;
    let t = 400;
    for (const id of ids.slice(1)) {
      s = run(
        [move(300, 400, t, item(id, "centre", "pantry")), timer(t + COLLECT_DWELL_MS)],
        s,
      ).state;
      t += 300;
    }
    expect(s.stack).toHaveLength(STACK_MAX);
  });

  it("GEST-04 dropping a stack on a bin moves the items that aren't there yet [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    const c = run([move(300, 400, 500, item("dumplings")), timer(750)], d.state);
    expect(c.state.stack).toEqual(["eggs", "dumplings"]);
    const r = run(
      [
        move(200, 800, 800, { kind: "bin", to: "freezer" }),
        up(200, 800, 820, { kind: "bin", to: "freezer" }),
      ],
      c.state,
    );
    expect(r.effects).toContainEqual({ type: "move", itemIds: ["eggs"], to: "freezer" });
    expect(r.effects).toContainEqual({ type: "haptic", event: "tick" });
  });

  it("GEST-04 the HERE bin refuses a drop only when every item is already there [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    expect(isHere(d.state, "fridge")).toBe(true);
    const r = run([up(200, 800, 600, { kind: "bin", to: "fridge" })], d.state);
    expect(r.effects).toEqual([
      { type: "haptic", event: "blocked" },
      { type: "blocked", to: "fridge" },
      { type: "home" },
    ]);
  });

  it("GEST-04 dragging onto the freezer area of the fridge is a move, not a reorder [FR-G02] [FR-G06] [FR-G07]", () => {
    const d = dragging();
    const over = run([move(100, 200, 500, { kind: "section", section: "freezer" })], d.state);
    expect(over.state.overSection).toBe("freezer");
    const r = run([up(100, 200, 520, { kind: "section", section: "freezer" })], over.state);
    expect(r.effects).toEqual([{ type: "move", itemIds: ["eggs"], to: "freezer" }]);
  });

  it("GEST-04 select mode: holding a picked item lifts every picked item as one stack [FR-G02] [FR-G06] [FR-G07]", () => {
    const r = lifted({ selectMode: true, picked: ["milk", "eggs", "tofu"] });
    expect(r.state.phase).toBe("COLLECTING");
    expect(r.state.stack).toEqual(["eggs", "milk", "tofu"]);
    const unpicked = lifted({ selectMode: true, picked: ["milk"] });
    expect(unpicked.state.phase).toBe("DRAGGING");
    expect(unpicked.state.stack).toEqual(["eggs"]);
  });
});

describe("cancel paths (FR-G01..G07)", () => {
  const states: Array<[string, () => GestureState]> = [
    ["PRESSING", () => run([down()]).state],
    ["LIFTED", () => lifted().state],
    ["MENU_TRACKING", () => run([move(CENTRE.x, CENTRE.y - 20, 400)], lifted().state).state],
    ["DRAGGING", () => dragging().state],
    [
      "COLLECTING",
      () => run([move(300, 400, 500, item("kimchi")), timer(750)], dragging().state).state,
    ],
  ];
  const cancels: Array<[string, GestureEvent]> = [
    ["a second pointer", { type: "pointer", pointerId: 2 }],
    ["pointercancel", { type: "cancel" }],
    ["Escape", { type: "cancel" }],
    ["page hidden", { type: "cancel" }],
  ];
  for (const [phase, make] of states) {
    for (const [why, ev] of cancels) {
      it(`GEST-05 ${why} during ${phase} returns to IDLE with no action and no haptic [FR-G01] [FR-G06]`, () => {
        const r = run([ev], make());
        expect(r.state.phase).toBe("IDLE");
        expect(r.effects.every((e) => e.type === "home")).toBe(true);
      });
    }
  }

  it("GEST-05 a second finger landing on another item cancels too [FR-G01] [FR-G06]", () => {
    const r = run([down({ pointerId: 2, itemId: "milk" })], dragging().state);
    expect(r.state.phase).toBe("IDLE");
  });

  it("GEST-05 events from another pointer are ignored mid-drag [FR-G01] [FR-G06]", () => {
    const d = dragging();
    const r = run(
      [{ type: "move", pointerId: 9, point: { x: 0, y: 0 }, time: 500, hit: NONE }],
      d.state,
    );
    expect(r.state).toBe(d.state);
  });
});

describe("live changes under a gesture (SYNC-G2, SYNC-G6)", () => {
  it("SYNC-G2 an item used up mid-drag leaves the stack and the drop moves the rest [FR-G06]", () => {
    const c = run([move(300, 400, 500, item("kimchi")), timer(750)], dragging().state);
    const rest = without(ITEMS, "kimchi");
    const synced = run([{ type: "sync", items: rest }], c.state);
    expect(synced.state.stack).toEqual(["eggs"]);
    expect(synced.state.dropped).toEqual(["kimchi"]);
    const r = run([up(200, 800, 900, { kind: "bin", to: "pantry" })], synced.state);
    expect(r.effects).toEqual([{ type: "move", itemIds: ["eggs"], to: "pantry" }]);
  });

  it("SYNC-G2 if the whole stack is gone the drag ends [FR-G06]", () => {
    const rest = without(ITEMS, "eggs");
    const r = run([{ type: "sync", items: rest }], dragging().state);
    expect(r.state.phase).toBe("IDLE");
  });

  it("SYNC-G6 new items join the end of the section and the dragged item stays in hand [FR-G02]", () => {
    const d = run([move(300, 400, 500, item("yogurt", "after"))], dragging().state);
    const r = run([{ type: "sync", items: { ...ITEMS, butter: "fridge" } }], d.state);
    expect(r.state.phase).toBe("DRAGGING");
    expect(r.state.order).toEqual(["milk", "yogurt", "eggs", "kimchi", "tofu", "butter"]);
    const done = run([up(300, 400, 600, item("yogurt", "after"))], r.state);
    expect(done.effects).toContainEqual({
      type: "reorder",
      section: "fridge",
      order: ["milk", "yogurt", "eggs", "kimchi", "tofu", "butter"],
    });
  });
});

describe("personal order with a filter on (FR-G02)", () => {
  it("GEST-04 hidden items keep their places when the visible ones are reordered [FR-G02] [FR-G06] [FR-G07]", () => {
    expect(mergeOrder(["a", "b", "c", "d", "e"], ["d", "b"])).toEqual(["a", "d", "c", "b", "e"]);
    expect(mergeOrder(["a", "b"], ["b", "a", "new"])).toEqual(["b", "a", "new"]);
  });
});
