"use client";

/**
 * The thin pointer hook around the gesture reducer (SPEC §2 "Browser plumbing"):
 * Pointer Events with capture; native vertical scroll until the lift, then a non-passive
 * `touchmove` that keeps the page still; cancel on pointercancel, a second pointer, Escape or a
 * hidden page; one cached grid of cell rects taken at lift (scroll-compensated) for hit tests, so
 * nothing reads layout while the finger moves. Effects go to the screen; haptics fire here.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { haptic } from "@/lib/haptics";
import { AUTOSCROLL_EDGE_PX } from "./constants";
import {
  holdsPage,
  initialGesture,
  nextDeadline,
  reduce,
  type Effect,
  type GestureEvent,
  type GestureState,
  type Hit,
  type Loc,
} from "./reducer";

type Rect = { left: number; top: number; width: number; height: number };
type Grid = {
  scrollY: number;
  /**
   * Each section's cell rects keyed by slot: the tile's position in the section's full list
   * (data-index). Virtualized areas only render the rows near the viewport, so only those slots
   * are known; the grid is measured again after auto-scroll and live changes.
   */
  cells: Map<Loc, Map<number, Rect>>;
  areas: Array<{ section: Loc; rect: Rect }>;
  bins: Array<{ to: Loc; rect: Rect }> | null;
  /** Top of the bottom bar: the bottom auto-scroll band ends here and tiles under it can't be hit. */
  barTop: number | null;
};

export type GestureOptions = {
  selectMode: boolean;
  picked: readonly string[];
  /** Every item on screen and where it is. */
  items: Readonly<Record<string, Loc>>;
  /** Each section's items in display order. */
  orders: Readonly<Partial<Record<Loc, readonly string[]>>>;
  onEffect: (effect: Effect, state: GestureState) => void;
};

const now = () => performance.now();

function rectOf(el: Element): Rect {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, height: r.height };
}

/** The tile's own place in the grid: its rect without the reorder shift it may be wearing. */
function slotRectOf(el: HTMLElement): Rect {
  const r = rectOf(el);
  const m = /translate3d\(([-\d.]+)px, ([-\d.]+)px/.exec(el.style.transform);
  return m ? { ...r, left: r.left - Number(m[1]), top: r.top - Number(m[2]) } : r;
}

function inside(r: Rect, x: number, y: number): boolean {
  return x >= r.left && x <= r.left + r.width && y >= r.top && y <= r.top + r.height;
}

export function usePlaceGestures(
  rootRef: React.RefObject<HTMLElement | null>,
  opts: GestureOptions,
) {
  const [state, setState] = useState<GestureState>(initialGesture);
  const stateRef = useRef<GestureState>(initialGesture);
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const grid = useRef<Grid | null>(null);
  const timer = useRef<number | null>(null);
  const scrollLoop = useRef<number | null>(null);
  const finger = useRef<{ x: number; y: number } | null>(null);
  /**
   * The tile's native click after a hold or a drag must not also open the sheet. Browsers send it
   * right after pointerup (or not at all after a long-press), so the flag only lasts a moment.
   */
  const lifted = useRef(false);
  const swallowUntil = useRef(0);
  /** The tile that opened the static menu from the keyboard or a right-click: focus goes back. */
  const menuInvoker = useRef<HTMLElement | null>(null);

  const measure = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    const cells = new Map<Loc, Map<number, Rect>>();
    for (const area of root.querySelectorAll<HTMLElement>("[data-kt-area]")) {
      const slots = new Map<number, Rect>();
      for (const tile of area.querySelectorAll<HTMLElement>("[data-item-id][data-index]")) {
        slots.set(Number(tile.dataset.index), slotRectOf(tile));
      }
      cells.set(area.dataset.ktArea as Loc, slots);
    }
    const bar = document.querySelector<HTMLElement>(".kt-action-bar");
    grid.current = {
      scrollY: window.scrollY,
      cells,
      areas: [...root.querySelectorAll<HTMLElement>("[data-kt-area]")].map((el) => ({
        section: el.dataset.ktArea as Loc,
        rect: rectOf(el),
      })),
      bins: grid.current?.bins ?? null,
      barTop: bar ? bar.getBoundingClientRect().top : null,
    };
  }, [rootRef]);

  /**
   * What each slot shows: while reordering, the lifted item's section shows the reducer's order;
   * otherwise its order at lift (the screen keeps that section in this order until IDLE). Other
   * sections show the screen's order.
   */
  const shownOrder = (section: Loc): readonly string[] => {
    const s = stateRef.current;
    if (section === s.section && s.phase !== "IDLE") {
      return s.phase === "DRAGGING" ? s.order : s.original;
    }
    return optsRef.current.orders[section] ?? [];
  };

  const hitAt = useCallback((x: number, y: number): Hit => {
    const g = grid.current;
    const s = stateRef.current;
    if (!g) return { kind: "none" };
    if (!g.bins) {
      const bins = [...document.querySelectorAll<HTMLElement>("[data-kt-bin]")];
      if (bins.length)
        g.bins = bins.map((el) => ({ to: el.dataset.ktBin as Loc, rect: rectOf(el) }));
    }
    for (const b of g.bins ?? []) if (inside(b.rect, x, y)) return { kind: "bin", to: b.to };
    // The bottom bar covers the tiles scrolled under it.
    if (g.barTop !== null && y >= g.barTop) return { kind: "none" };
    // Cached rects are page-relative to the scroll at the last measure.
    const dy = window.scrollY - g.scrollY;
    const py = y + dy;
    for (const [section, slots] of g.cells) {
      const shown = shownOrder(section);
      for (const [k, r] of slots) {
        if (!inside(r, x, py)) continue;
        const id = shown[k];
        if (!id || id === s.itemId) return { kind: "section", section };
        const third = r.width / 3;
        const zone = x < r.left + third ? "before" : x > r.left + 2 * third ? "after" : "centre";
        return { kind: "item", id, zone, section };
      }
    }
    for (const a of g.areas)
      if (inside(a.rect, x, py)) return { kind: "section", section: a.section };
    return { kind: "none" };
  }, []);

  const stopAutoScroll = () => {
    if (scrollLoop.current !== null) cancelAnimationFrame(scrollLoop.current);
    scrollLoop.current = null;
  };

  const dispatch = useCallback(
    (ev: GestureEvent) => {
      const prev = stateRef.current;
      const step = reduce(prev, ev);
      const next = step.state;
      if (prev.phase === "PRESSING" && next.phase !== "PRESSING" && next.phase !== "IDLE") {
        // Lifted: take the grid now (SPEC §2 plumbing 6).
        lifted.current = true;
        measure();
      }
      if (prev.phase === "PRESSING" && next.phase === "IDLE" && ev.type === "move") {
        // A mouse that moved off before the lift still clicks the tile on release: not a tap.
        swallowUntil.current = now() + 400;
      }
      if (prev.phase === "MENU_STATIC" && next.phase === "IDLE" && menuInvoker.current) {
        // Back to the item the menu came from, or to Undo if it just left this screen.
        const back = menuInvoker.current;
        menuInvoker.current = null;
        window.setTimeout(() => {
          if (back.isConnected) back.focus();
          else document.querySelector<HTMLElement>(".kt-toast__undo")?.focus();
        }, 60);
      }
      if (ev.type === "down" && prev.phase === "IDLE") {
        // A new press: only the click that ends a hold or a drag is ignored, never a later tap's.
        lifted.current = false;
        swallowUntil.current = 0;
      }
      if (lifted.current && (next.phase === "IDLE" || next.phase === "MENU_STATIC")) {
        lifted.current = false;
        swallowUntil.current = now() + 400;
      }
      stateRef.current = next;
      if (next !== prev) setState(next);
      for (const effect of step.effects) {
        if (effect.type === "haptic") haptic(effect.event, { reorder: effect.reorder });
        else optsRef.current.onEffect(effect, prev);
      }
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      const due = nextDeadline(next);
      if (due !== null) {
        timer.current = window.setTimeout(
          () => dispatch({ type: "timer", time: now() }),
          Math.max(0, due - now()),
        );
      }
      if (next.phase === "IDLE") {
        grid.current = null;
        finger.current = null;
        stopAutoScroll();
      }
    },
    [measure],
  );

  // Live data under a gesture (SYNC-G2, SYNC-G6): items gone from the screen leave the stack.
  const itemsKey = Object.entries(opts.items)
    .map(([k, v]) => `${k}:${v}`)
    .join(",");
  useEffect(() => {
    if (stateRef.current.phase === "IDLE") return;
    dispatch({ type: "sync", items: optsRef.current.items });
    // Measure again once the screen has drawn the change (SYNC-G6).
    requestAnimationFrame(() => {
      if (stateRef.current.phase !== "IDLE" && grid.current) measure();
    });
  }, [itemsKey, dispatch, measure]);

  /** Auto-scroll speed for a finger at y: the top edge, or the band just above the bottom bar. */
  const scrollSpeed = (y: number): number => {
    const edge = AUTOSCROLL_EDGE_PX;
    const barTop = grid.current?.barTop ?? window.innerHeight;
    if (y < edge) return -Math.min(14, (edge - y) / 4);
    if (y >= barTop - edge && y < barTop) return Math.min(14, (y - (barTop - edge)) / 4);
    return 0;
  };

  const autoScroll = useCallback(() => {
    stopAutoScroll();
    const step = () => {
      const s = stateRef.current;
      const f = finger.current;
      if (!f || (s.phase !== "DRAGGING" && s.phase !== "COLLECTING")) return stopAutoScroll();
      const speed = scrollSpeed(f.y);
      if (speed === 0) return stopAutoScroll();
      window.scrollBy(0, speed);
      // Rows a virtualized area just rendered join the grid.
      measure();
      dispatch({
        type: "move",
        pointerId: s.pointerId ?? 0,
        point: f,
        time: now(),
        hit: hitAt(f.x, f.y),
        scrolling: true,
      });
      scrollLoop.current = requestAnimationFrame(step);
    };
    scrollLoop.current = requestAnimationFrame(step);
  }, [dispatch, hitAt, measure]);

  // Window listeners while a pointer is down; page-level cancels at any time.
  useEffect(() => {
    const onMove = (e: globalThis.PointerEvent) => {
      const s = stateRef.current;
      if (s.phase === "IDLE" || s.phase === "MENU_STATIC" || e.pointerId !== s.pointerId) return;
      const point = { x: e.clientX, y: e.clientY };
      finger.current = point;
      dispatch({
        type: "move",
        pointerId: e.pointerId,
        point,
        time: now(),
        hit: hitAt(point.x, point.y),
      });
      const after = stateRef.current;
      if (
        (after.phase === "DRAGGING" || after.phase === "COLLECTING") &&
        scrollLoop.current === null
      ) {
        if (scrollSpeed(point.y) !== 0) autoScroll();
      }
    };
    const onUp = (e: globalThis.PointerEvent) => {
      const s = stateRef.current;
      if (s.phase === "IDLE" || e.pointerId !== s.pointerId) return;
      const point = { x: e.clientX, y: e.clientY };
      dispatch({
        type: "up",
        pointerId: e.pointerId,
        point,
        time: now(),
        hit: hitAt(point.x, point.y),
      });
    };
    const onCancel = (e: globalThis.PointerEvent) => {
      if (e.pointerId === stateRef.current.pointerId) dispatch({ type: "cancel" });
    };
    const onDown = (e: globalThis.PointerEvent) => {
      const s = stateRef.current;
      if (s.phase !== "IDLE" && s.phase !== "MENU_STATIC" && e.pointerId !== s.pointerId) {
        dispatch({ type: "pointer", pointerId: e.pointerId });
      }
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      // A key press means the next click is the keyboard's (Enter, Space), never a hold's.
      swallowUntil.current = 0;
      if (e.key === "Escape" && stateRef.current.phase !== "IDLE") dispatch({ type: "cancel" });
    };
    const onHidden = () => {
      if (document.visibilityState === "hidden") dispatch({ type: "cancel" });
    };
    const onScroll = () => {
      if (stateRef.current.phase === "MENU_STATIC") dispatch({ type: "cancel" });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("pointerdown", onDown, true);
    window.addEventListener("keydown", onKey);
    document.addEventListener("visibilitychange", onHidden);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("visibilitychange", onHidden);
      window.removeEventListener("scroll", onScroll);
      if (timer.current !== null) window.clearTimeout(timer.current);
      stopAutoScroll();
    };
  }, [dispatch, hitAt, autoScroll]);

  // From the lift on, the page must not scroll under the finger (SPEC §2 plumbing 2). Registered
  // for the screen's lifetime: a listener added mid-touch is too late for some browsers.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const onTouchMove = (e: TouchEvent) => {
      if (holdsPage(stateRef.current)) e.preventDefault();
    };
    root.addEventListener("touchmove", onTouchMove, { passive: false });
    return () => root.removeEventListener("touchmove", onTouchMove);
  }, [rootRef]);

  /** Handlers for one tile. */
  const bind = useCallback(
    (itemId: string) => ({
      onPointerDown: (e: PointerEvent<HTMLElement>) => {
        if (e.button !== 0 || !e.isPrimary) return;
        const o = optsRef.current;
        const section = o.items[itemId];
        if (!section) return;
        // The fan centres on the icon circle, not the whole tile.
        const r = (
          e.currentTarget.querySelector(".kt-tile__icon") ?? e.currentTarget
        ).getBoundingClientRect();
        try {
          e.currentTarget.setPointerCapture(e.pointerId);
        } catch {
          // not capturable (synthetic events): window listeners still see the pointer
        }
        dispatch({
          type: "down",
          itemId,
          pointerId: e.pointerId,
          point: { x: e.clientX, y: e.clientY },
          time: now(),
          centre: { x: r.left + r.width / 2, y: r.top + r.height / 2 },
          viewport: { width: window.innerWidth, height: window.innerHeight },
          selectMode: o.selectMode,
          picked: o.picked,
          items: o.items,
          order: o.orders[section] ?? [],
        });
      },
      onContextMenu: (e: React.MouseEvent<HTMLElement>) => {
        e.preventDefault();
        // Right-click with a mouse opens the menu; a long-press's context menu is just swallowed.
        const pointerType = (e.nativeEvent as globalThis.PointerEvent).pointerType;
        if (pointerType === "touch" || pointerType === "pen" || optsRef.current.selectMode) return;
        if (stateRef.current.phase !== "IDLE") return;
        openMenu(itemId, e.currentTarget);
      },
      onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
        if ((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") {
          if (optsRef.current.selectMode) return;
          e.preventDefault();
          openMenu(itemId, e.currentTarget);
        }
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dispatch],
  );

  const openMenu = useCallback(
    (itemId: string, el: HTMLElement) => {
      const r = (el.querySelector(".kt-tile__icon") ?? el).getBoundingClientRect();
      menuInvoker.current = el;
      dispatch({
        type: "menu",
        itemId,
        centre: { x: r.left + r.width / 2, y: r.top + r.height / 2 },
        viewport: { width: window.innerWidth, height: window.innerHeight },
        time: now(),
      });
    },
    [dispatch],
  );

  const choose = useCallback((index: 0 | 1 | 2) => dispatch({ type: "choose", index }), [dispatch]);
  /** True once for the click that ends a hold or a drag: the screen ignores that click. */
  const consumeClick = useCallback(() => {
    const swallow = now() < swallowUntil.current;
    swallowUntil.current = 0;
    return swallow;
  }, []);
  const cancel = useCallback(() => dispatch({ type: "cancel" }), [dispatch]);

  /** Where a tile should be drawn while reordering (transform only, SPEC §2 plumbing 6). */
  const shiftOf = useCallback(
    (id: string): { x: number; y: number } | null => {
      const s = state;
      const g = grid.current;
      if (s.phase !== "DRAGGING" || !g || !s.section) return null;
      const slots = g.cells.get(s.section);
      const from = s.original.indexOf(id);
      const to = s.order.indexOf(id);
      if (!slots || from < 0 || to < 0 || from === to) return null;
      const a = slots.get(from);
      const b = slots.get(to);
      if (!a || !b) return null;
      return { x: b.left - a.left, y: b.top - a.top };
    },
    [state],
  );

  /** The lifted item's rect at lift (for the clone that rises above the scrim). */
  const liftedRect = useCallback((): Rect | null => {
    const g = grid.current;
    const s = state;
    if (!g || !s.itemId || !s.section) return null;
    const r = g.cells.get(s.section)?.get(s.original.indexOf(s.itemId));
    if (!r) return null;
    return { ...r, top: r.top - (window.scrollY - g.scrollY) };
  }, [state]);

  return { state, bind, choose, cancel, consumeClick, openMenu, shiftOf, liftedRect };
}
