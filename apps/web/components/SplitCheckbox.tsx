"use client";

import type { KeyboardEvent, Ref } from "react";
import { memberColors } from "@/lib/members";
import { strings } from "@/lib/strings";

/** Tilts for stamped boxes, picked by (row + col) % 4 so a cell never changes tilt. */
export const SPLIT_TILTS = [-4, 3, -2, 4] as const;

export function checkboxTilt(row: number, col: number): number {
  const index = (((row + col) % SPLIT_TILTS.length) + SPLIT_TILTS.length) % SPLIT_TILTS.length;
  return SPLIT_TILTS[index]!;
}

export type SplitCheckboxProps = {
  memberName: string;
  itemName: string;
  initial: string;
  /** 0-based join order of the member (color slot). */
  colorIndex: number;
  checked: boolean;
  onToggle?: () => void;
  row: number;
  col: number;
  /** Not your column (member mode) or a read-only bill. */
  disabled?: boolean;
  /** Waiting for the server to acknowledge; shown at 50% opacity. */
  pending?: boolean;
  /** Roving tabindex (managed by SplitGrid). */
  tabIndex?: number;
  onKeyDown?: (event: KeyboardEvent<HTMLButtonElement>) => void;
  onFocus?: () => void;
  buttonRef?: Ref<HTMLButtonElement>;
};

/**
 * 44 px hit area around a 30 px box. Off: 2 px --control-edge outline. On: member color fill,
 * initial, 2 px ink border, 2 px offset shadow, fixed tilt; stamps 1.15 → 1 in 120 ms.
 */
export function SplitCheckbox({
  memberName,
  itemName,
  initial,
  colorIndex,
  checked,
  onToggle,
  row,
  col,
  disabled = false,
  pending = false,
  tabIndex,
  onKeyDown,
  onFocus,
  buttonRef,
}: SplitCheckboxProps) {
  const colors = memberColors(colorIndex);
  return (
    <button
      ref={buttonRef}
      type="button"
      className="kt-split-box"
      aria-pressed={checked}
      aria-label={strings.split.checkboxLabel(memberName, itemName)}
      disabled={disabled}
      data-pending={pending || undefined}
      tabIndex={tabIndex}
      onFocus={onFocus}
      onClick={() => onToggle?.()}
      onKeyDown={(event) => {
        // Space toggles on keydown; the native keyup activation is cancelled below so the
        // box never flips twice.
        if (event.key === " " || event.key === "Spacebar") {
          event.preventDefault();
          if (!disabled) onToggle?.();
          return;
        }
        onKeyDown?.(event);
      }}
      onKeyUp={(event) => {
        if (event.key === " " || event.key === "Spacebar") event.preventDefault();
      }}
    >
      <span
        className="kt-split-box__box"
        data-on={checked || undefined}
        aria-hidden="true"
        style={
          checked
            ? {
                background: colors.fill,
                color: colors.ink,
                ["--tilt" as string]: `${checkboxTilt(row, col)}deg`,
              }
            : undefined
        }
      >
        {checked ? initial : null}
      </span>
    </button>
  );
}
