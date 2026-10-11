"use client";

import { useRef } from "react";
import type { InventoryItem, KitchenFilter } from "@/lib/api/kitchen-types";
import { ROW_H, VIRTUALIZE_AT, chunk, useColumns, useWindowRows } from "@/lib/area-grid";
import { matchesFilter, showsOpenBadge, tileLabel } from "@/lib/kitchen";
import { strings } from "@/lib/strings";
import { FoodIcon, foodPastel } from "./FoodIcon";
import { OwnerDots } from "./OwnerDots";
import { CheckIcon, LockIcon } from "./icons";

/** Kitchen gestures on a tile (docs/features/kitchen-gestures/SPEC.md). */
export type TileGestures = {
  handlers: (itemId: string) => {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => void;
    onContextMenu: (e: React.MouseEvent<HTMLElement>) => void;
    onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => void;
  };
  /** Reorder: slide aside by this much (transform only). */
  shiftOf: (itemId: string) => { x: number; y: number } | null;
  /** Select mode: every tile shows a check circle; picked ones are ticked. */
  selectMode: boolean;
  picked: ReadonlySet<string>;
  /** The lifted item's own cell while it rides under the finger. */
  gapId: string | null;
  /** Collected items' cells show dashed ghosts. */
  ghostIds: ReadonlySet<string>;
  /** Items in hand: their rows stay rendered in a virtualized area until the gesture ends. */
  pinned: ReadonlySet<string>;
  /** The item being pressed (the charge ring shows on it). */
  pressedId: string | null;
};

export type ItemTileProps = {
  item: InventoryItem;
  meMemberId: string | null;
  /** Filtered out: drops to 22% opacity so nothing moves (FR-66). */
  dimmed?: boolean;
  /** Pop in (first time this member sees the item; only when motion is allowed). */
  pop?: boolean;
  onOpen: (item: InventoryItem) => void;
  gestures?: TileGestures;
  /** Position in the area's full list: the gesture engine's slot (data-index). */
  index?: number;
};

/**
 * Item tile (PRD §19.8 anatomy, FR-67): 40 px pastel circle with a 24 px icon, 16 px badge (lime
 * check = open, paper lock = ask first), SOON tag and dashed orange ring when due, mono 9 short
 * name, 13 px owner avatars. The label states name, owners, share state and use-soon.
 */
export function ItemTile({
  item,
  meMemberId,
  dimmed = false,
  pop = false,
  onOpen,
  gestures,
  index,
}: ItemTileProps) {
  const open = showsOpenBadge(item);
  const shift = gestures?.shiftOf(item.id) ?? null;
  const selectMode = gestures?.selectMode ?? false;
  const picked = gestures?.picked.has(item.id) ?? false;
  // On the gesture screens items also say where they are (SPEC §8): moves change it.
  const label = gestures
    ? `${tileLabel(item, meMemberId)}, ${strings.gestures.inPlace(strings.gestures.placeLower[item.place])}`
    : tileLabel(item, meMemberId);
  return (
    <button
      type="button"
      className="kt-tile"
      aria-label={
        selectMode
          ? `${label}, ${picked ? strings.gestures.tileSelected : strings.gestures.tileNotSelected}`
          : label
      }
      aria-pressed={selectMode ? picked : undefined}
      data-dim={dimmed || undefined}
      data-soon={item.use_soon || undefined}
      data-pop={pop || undefined}
      data-picked={picked || undefined}
      data-select={selectMode || undefined}
      data-gap={gestures?.gapId === item.id || undefined}
      data-ghost={gestures?.ghostIds.has(item.id) || undefined}
      data-shifted={shift ? "" : undefined}
      style={shift ? { transform: `translate3d(${shift.x}px, ${shift.y}px, 0)` } : undefined}
      data-item-id={item.id}
      data-index={index}
      data-pressed={gestures?.pressedId === item.id || undefined}
      onClick={() => onOpen(item)}
      {...gestures?.handlers(item.id)}
    >
      {selectMode ? (
        <span className="kt-tile__pick" aria-hidden="true">
          {picked ? <CheckIcon size={12} strokeWidth={3.5} /> : null}
        </span>
      ) : null}
      <span className="kt-tile__icon" style={{ background: foodPastel(item.icon_key, item.place) }}>
        <FoodIcon iconKey={item.icon_key} place={item.place} />
        <span className="kt-tile__badge" data-open={open || undefined}>
          {open ? <CheckIcon size={8} strokeWidth={4} /> : <LockIcon size={8} strokeWidth={3.5} />}
        </span>
        {item.use_soon ? <span className="kt-tile__soon">{strings.place.soonTag}</span> : null}
      </span>
      <span className="kt-tile__name">{item.short_name || item.name}</span>
      <OwnerDots owners={item.owners} />
    </button>
  );
}

export type AreaGridProps = {
  items: readonly InventoryItem[];
  filter: KitchenFilter;
  meMemberId: string | null;
  label: string;
  popIds?: ReadonlySet<string>;
  onOpen: (item: InventoryItem) => void;
  gestures?: TileGestures;
};

/**
 * The tiles of one area, wrapping onto rows (nothing hidden, FR-65). Past VIRTUALIZE_AT tiles only
 * the rows near the viewport are rendered; the area keeps its full height so the page scrolls to
 * every item (KITCH-16, EDGE-19-10).
 */
export function AreaGrid({
  items,
  filter,
  meMemberId,
  label,
  popIds,
  onOpen,
  gestures,
}: AreaGridProps) {
  const ref = useRef<HTMLDivElement>(null);
  const columns = useColumns(ref);
  const virtual = items.length > VIRTUALIZE_AT;
  const rows = chunk(items, columns);
  const { start, end } = useWindowRows(ref, rows.length, virtual);
  // The rows near the viewport, plus any row holding an item in hand: a finger keeps pressing a
  // tile that must not unmount mid-drag.
  const shownRows: number[] = [];
  if (virtual) {
    const keep = new Set<number>();
    for (let r = start; r < end; r++) keep.add(r);
    if (gestures?.pinned.size) {
      items.forEach((item, i) => {
        if (gestures.pinned.has(item.id)) keep.add(Math.floor(i / columns));
      });
    }
    shownRows.push(...[...keep].sort((a, b) => a - b));
  }

  const tile = (item: InventoryItem, index: number) => (
    <ItemTile
      key={item.id}
      index={index}
      item={item}
      meMemberId={meMemberId}
      dimmed={!matchesFilter(item, filter)}
      pop={popIds?.has(item.id)}
      onOpen={onOpen}
      gestures={gestures}
    />
  );

  return (
    <div
      ref={ref}
      className="kt-area__tiles"
      role="group"
      aria-label={label}
      data-virtual={virtual || undefined}
      style={virtual ? { height: rows.length * ROW_H } : undefined}
    >
      {virtual
        ? shownRows.map((r) => (
            <div key={r} className="kt-area__row" style={{ top: r * ROW_H }}>
              {rows[r]!.map((item, c) => tile(item, r * columns + c))}
            </div>
          ))
        : items.map(tile)}
    </div>
  );
}
