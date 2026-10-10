"use client";

import { useEffect, useLayoutEffect, useState, type RefObject } from "react";

/**
 * Layout math for fridge and pantry areas (PRD §19.8: tiles 56×74, five per row at 390 px; §19.9:
 * a very full area virtualizes and everything stays reachable by scrolling the page, never an
 * inner scroll panel). Rows are a fixed 84 px (74 px tile + 10 px gap), which is also the shelf
 * pitch drawn behind the tiles.
 */
export const TILE_W = 56;
export const GAP_X = 6;
export const ROW_H = 84;
/** Areas with more tiles than this render only the rows near the viewport. */
export const VIRTUALIZE_AT = 40;
export const OVERSCAN_ROWS = 4;

export function columnsFor(width: number): number {
  if (!Number.isFinite(width) || width <= 0) return 5;
  return Math.max(1, Math.floor((width + GAP_X) / (TILE_W + GAP_X)));
}

export function chunk<T>(items: readonly T[], size: number): T[][] {
  const rows: T[][] = [];
  for (let i = 0; i < items.length; i += size) rows.push(items.slice(i, i + size));
  return rows;
}

/**
 * Rows to render: those intersecting the viewport plus `overscan` either side. `top` is the area's
 * top edge relative to the viewport (getBoundingClientRect().top).
 */
export function visibleRows(
  top: number,
  viewportHeight: number,
  rowCount: number,
  overscan = OVERSCAN_ROWS,
  rowHeight = ROW_H,
): { start: number; end: number } {
  if (rowCount <= 0) return { start: 0, end: 0 };
  const first = Math.floor(-top / rowHeight) - overscan;
  const last = Math.ceil((viewportHeight - top) / rowHeight) + overscan;
  const start = Math.min(Math.max(0, first), rowCount);
  const end = Math.max(start, Math.min(rowCount, last));
  return { start, end };
}

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** Columns that fit the element's width, kept current with a ResizeObserver. */
export function useColumns(ref: RefObject<HTMLElement | null>): number {
  const [columns, setColumns] = useState(5);
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setColumns(columnsFor(el.clientWidth));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return columns;
}

/** The row window for a page-scrolled list; recomputed on scroll and resize (one per frame). */
export function useWindowRows(
  ref: RefObject<HTMLElement | null>,
  rowCount: number,
  enabled: boolean,
): { start: number; end: number } {
  const [range, setRange] = useState(() => ({ start: 0, end: Math.min(rowCount, 10) }));
  useIsoLayoutEffect(() => {
    if (!enabled) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const el = ref.current;
      if (!el) return;
      const next = visibleRows(el.getBoundingClientRect().top, window.innerHeight, rowCount);
      setRange((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [ref, rowCount, enabled]);
  return enabled ? range : { start: 0, end: rowCount };
}
