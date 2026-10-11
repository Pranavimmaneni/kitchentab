"use client";

/**
 * What the gestures draw over the Fridge and Pantry (SPEC §2–§4, renders m*, c*, s*): the scrim,
 * the lifted item, the quick-action fan, the stack under the finger, the drop bar and the select
 * bar. Positions come from the reducer and the hook; everything moves with transform/opacity.
 */
import { useEffect, useRef, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { FoodIcon, foodPastel } from "@/components/FoodIcon";
import {
  XIcon,
  EmptyIcon,
  FridgeIcon,
  HandIcon,
  PantryIcon,
  SnowflakeIcon,
  CheckIcon,
} from "@/components/icons";
import type { InventoryItem, Place } from "@/lib/api/kitchen-types";
import { gestureStrings } from "@/features/kitchen/gestures/strings";
import type { BubbleAction } from "./actions";
import { RIDE_ABOVE_PX } from "./constants";
import { isHere, type GestureState } from "./reducer";

const PLACE_ICON: Record<Place, (p: { size?: number }) => ReactNode> = {
  freezer: SnowflakeIcon,
  fridge: FridgeIcon,
  pantry: PantryIcon,
};

export function PlaceIcon({ place, size = 22 }: { place: Place; size?: number }) {
  const Icon = PLACE_ICON[place];
  return <Icon size={size} />;
}

function bubbleIcon(action: BubbleAction) {
  switch (action.kind) {
    case "move":
      return <PlaceIcon place={action.to} size={24} />;
    case "ask":
      return <HandIcon size={24} />;
    case "usedUp":
      return <EmptyIcon size={24} />;
    case "help":
      return <CheckIcon size={24} />;
    case "cancelAsk":
      return <XIcon size={24} />;
  }
}

export function bubbleLabel(action: BubbleAction): string {
  switch (action.kind) {
    case "move":
      return gestureStrings.places[action.to];
    case "ask":
      return gestureStrings.bubbleAsk(action.owner);
    case "usedUp":
      return gestureStrings.bubbleUsedUp;
    case "help":
      return gestureStrings.bubbleHelp;
    case "cancelAsk":
      return gestureStrings.bubbleCancelAsk;
  }
}

function bubbleChip(action: BubbleAction): string {
  switch (action.kind) {
    case "move":
      return gestureStrings.moveTo(gestureStrings.placeLower[action.to]);
    case "ask":
      return gestureStrings.chipAsk(action.owner);
    case "usedUp":
      return gestureStrings.chipUsedUp;
    case "help":
      return gestureStrings.chipHelp;
    case "cancelAsk":
      return gestureStrings.chipCancelAsk;
  }
}

/** The item as it rides above everything: the tile's icon circle, lifted (FR-G01). */
function Lifted({
  item,
  style,
  count,
}: {
  item: InventoryItem;
  style: CSSProperties;
  count?: number;
}) {
  return (
    <div className="kt-lifted" style={style} aria-hidden="true">
      <span
        className="kt-lifted__icon"
        style={{ background: foodPastel(item.icon_key, item.place) }}
      >
        <FoodIcon iconKey={item.icon_key} place={item.place} />
      </span>
      {count && count > 1 ? (
        <span className="kt-lifted__count">{count}</span>
      ) : (
        <span className="kt-lifted__name">{item.short_name || item.name}</span>
      )}
    </div>
  );
}

export type GestureOverlayProps = {
  state: GestureState;
  items: ReadonlyMap<string, InventoryItem>;
  bubbles: readonly [BubbleAction, BubbleAction, BubbleAction] | null;
  liftedAt: { left: number; top: number; width: number; height: number } | null;
  onChoose: (index: 0 | 1 | 2) => void;
  onCancel: () => void;
};

export function GestureOverlay({
  state,
  items,
  bubbles,
  liftedAt,
  onChoose,
  onCancel,
}: GestureOverlayProps) {
  const item = state.itemId ? items.get(state.itemId) : undefined;
  const menuOpen =
    state.phase === "LIFTED" || state.phase === "MENU_TRACKING" || state.phase === "MENU_STATIC";
  if (!item) return null;

  if (menuOpen && state.fan && bubbles) {
    // The tile's icon circle sits 0..40 px into the 56 px cell; the clone sits on it.
    const cx = liftedAt ? liftedAt.left + liftedAt.width / 2 : state.fan.origin.x;
    const cy = liftedAt ? liftedAt.top + 20 : state.fan.origin.y;
    return (
      <div className="kt-gesture-layer" data-phase={state.phase}>
        <div
          className="kt-gesture-scrim"
          aria-hidden="true"
          onClick={state.phase === "MENU_STATIC" ? onCancel : undefined}
        />
        <Lifted item={item} style={{ transform: `translate3d(${cx - 20}px, ${cy - 20}px, 0)` }} />
        <Fan
          state={state}
          bubbles={bubbles}
          interactive={state.phase === "MENU_STATIC"}
          name={item.short_name || item.name}
          onChoose={onChoose}
          onCancel={onCancel}
        />
      </div>
    );
  }

  if (state.phase === "DRAGGING" || state.phase === "COLLECTING") {
    const x = state.finger.x - 20;
    const y = state.finger.y - 20 - RIDE_ABOVE_PX;
    const gone = state.dropped.map((id) => items.get(id)?.short_name).filter(Boolean) as string[];
    return (
      <div className="kt-gesture-layer" data-phase={state.phase}>
        {state.phase === "DRAGGING" && state.fan && bubbles ? (
          // Drag won: the bubbles fold away (MENU_FOLD_MS) instead of vanishing.
          <div className="kt-fan" data-fold="" aria-hidden="true">
            {bubbles.map((action, i) => {
              const c = state.fan!.centres[i]!;
              return (
                <div
                  key={i}
                  className="kt-bubble"
                  style={{ transform: `translate3d(${c.x}px, ${c.y}px, 0)` }}
                >
                  <span className="kt-bubble__button">{bubbleIcon(action)}</span>
                </div>
              );
            })}
          </div>
        ) : null}
        <Lifted
          item={item}
          count={state.stack.length}
          style={{ transform: `translate3d(${x}px, ${y}px, 0)` }}
        />
        {gone.length ? (
          <p
            className="kt-gesture-note"
            role="status"
            style={{ transform: `translate3d(${x - 40}px, ${y - 34}px, 0)` }}
          >
            {gestureStrings.goneNote(gone[gone.length - 1]!)}
          </p>
        ) : null}
      </div>
    );
  }
  return null;
}

function Fan({
  state,
  bubbles,
  interactive,
  name,
  onChoose,
  onCancel,
}: {
  state: GestureState;
  bubbles: readonly [BubbleAction, BubbleAction, BubbleAction];
  interactive: boolean;
  name: string;
  onChoose: (index: 0 | 1 | 2) => void;
  onCancel: () => void;
}) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (interactive) first.current?.focus();
  }, [interactive]);
  const fan = state.fan!;
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const buttons = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=menuitem]")];
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      buttons[(at + 1) % buttons.length]?.focus();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      buttons[(at - 1 + buttons.length) % buttons.length]?.focus();
    } else if (e.key === "Escape" || e.key === "Tab") {
      e.preventDefault();
      onCancel();
    }
  };
  const hovered = state.hoverBubble;
  return (
    <div
      className="kt-fan"
      role={interactive ? "menu" : undefined}
      aria-label={interactive ? gestureStrings.menuLabel(name) : undefined}
      aria-hidden={interactive ? undefined : true}
      onKeyDown={interactive ? onKey : undefined}
    >
      {bubbles.map((action, i) => {
        const c = fan.centres[i]!;
        const hot = hovered === i;
        return (
          <div
            key={i}
            className="kt-bubble"
            data-hot={hot || undefined}
            style={{ transform: `translate3d(${c.x}px, ${c.y}px, 0)`, ["--i" as string]: i }}
          >
            {hot ? <span className="kt-bubble__chip">{bubbleChip(action)}</span> : null}
            {interactive ? (
              <button
                ref={i === 0 ? first : undefined}
                type="button"
                role="menuitem"
                className="kt-bubble__button"
                onClick={() => onChoose(i as 0 | 1 | 2)}
              >
                {bubbleIcon(action)}
                <span className="kt-bubble__label">{bubbleLabel(action)}</span>
              </button>
            ) : (
              <span className="kt-bubble__button">
                {bubbleIcon(action)}
                <span className="kt-bubble__label">{bubbleLabel(action)}</span>
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** While anything is dragged: three bins replace the bottom bar (FR-G07). */
export function DropBar({ state }: { state: GestureState }) {
  return (
    <div className="kt-drop-bar" aria-label={gestureStrings.dropBar} role="group">
      {(["freezer", "fridge", "pantry"] as const).map((p) => {
        const here = isHere(state, p);
        const name = gestureStrings.places[p];
        return (
          <div
            key={p}
            className="kt-bin"
            data-kt-bin={p}
            data-here={here || undefined}
            data-over={state.overBin === p || undefined}
            aria-label={gestureStrings.binLabel(name, here)}
          >
            <PlaceIcon place={p} size={24} />
            <span className="kt-bin__name">{here ? gestureStrings.here : name.toUpperCase()}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Select mode's bottom bar: the count and one button per destination (FR-G05). */
export function SelectBar({
  count,
  destinations,
  primary,
  busy,
  onMove,
}: {
  count: number;
  destinations: readonly Place[];
  primary: Place;
  busy: Place | null;
  onMove: (to: Place) => void;
}) {
  return (
    <div
      className="kt-select-bar"
      role="toolbar"
      aria-label={`${gestureStrings.selectBar}, ${gestureStrings.selectedLabel(count)}`}
    >
      <p className="kt-select-bar__count" aria-live="polite">
        <span className="kt-select-bar__n">{gestureStrings.selectedCount(count)}</span>
        <span className="kt-select-bar__word">{gestureStrings.selectedWord}</span>
      </p>
      {destinations.map((p) => (
        <button
          key={p}
          type="button"
          className="kt-pill kt-select-bar__move"
          data-variant={p === primary ? "primary" : "secondary"}
          data-size="52"
          disabled={count === 0 || busy !== null}
          aria-busy={busy === p || undefined}
          onClick={() => onMove(p)}
        >
          <PlaceIcon place={p} size={20} />
          <span>{gestureStrings.places[p]}</span>
        </button>
      ))}
    </div>
  );
}
