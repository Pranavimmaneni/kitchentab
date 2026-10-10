"use client";

import { useRef } from "react";
import type { InventoryItem, KitchenFilter } from "@/lib/api/kitchen-types";
import { ROW_H, VIRTUALIZE_AT, chunk, useColumns, useWindowRows } from "@/lib/area-grid";
import { matchesFilter, showsOpenBadge, tileLabel } from "@/lib/kitchen";
import { strings } from "@/lib/strings";
import { FoodIcon, foodPastel } from "./FoodIcon";
import { OwnerDots } from "./OwnerDots";
import { CheckIcon, LockIcon } from "./icons";

export type ItemTileProps = {
  item: InventoryItem;
  meMemberId: string | null;
  /** Filtered out: drops to 22% opacity so nothing moves (FR-66). */
  dimmed?: boolean;
  /** Pop in (first time this member sees the item; only when motion is allowed). */
  pop?: boolean;
  onOpen: (item: InventoryItem) => void;
};

/**
 * Item tile (PRD §19.8 anatomy, FR-67): 40 px pastel circle with a 24 px icon, 16 px badge (lime
 * check = open, paper lock = ask first), SOON tag and dashed orange ring when due, mono 9 short
 * name, 13 px owner avatars. The label states name, owners, share state and use-soon.
 */
export function ItemTile({ item, meMemberId, dimmed = false, pop = false, onOpen }: ItemTileProps) {
  const open = showsOpenBadge(item);
  return (
    <button
      type="button"
      className="kt-tile"
      aria-label={tileLabel(item, meMemberId)}
      data-dim={dimmed || undefined}
      data-soon={item.use_soon || undefined}
      data-pop={pop || undefined}
      data-item-id={item.id}
      onClick={() => onOpen(item)}
    >
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
};

/**
 * The tiles of one area, wrapping onto rows (nothing hidden, FR-65). Past VIRTUALIZE_AT tiles only
 * the rows near the viewport are rendered; the area keeps its full height so the page scrolls to
 * every item (KITCH-16, EDGE-19-10).
 */
export function AreaGrid({ items, filter, meMemberId, label, popIds, onOpen }: AreaGridProps) {
  const ref = useRef<HTMLDivElement>(null);
  const columns = useColumns(ref);
  const virtual = items.length > VIRTUALIZE_AT;
  const rows = chunk(items, columns);
  const { start, end } = useWindowRows(ref, rows.length, virtual);

  const tile = (item: InventoryItem) => (
    <ItemTile
      key={item.id}
      item={item}
      meMemberId={meMemberId}
      dimmed={!matchesFilter(item, filter)}
      pop={popIds?.has(item.id)}
      onOpen={onOpen}
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
        ? rows.slice(start, end).map((row, i) => (
            <div key={start + i} className="kt-area__row" style={{ top: (start + i) * ROW_H }}>
              {row.map(tile)}
            </div>
          ))
        : items.map(tile)}
    </div>
  );
}
