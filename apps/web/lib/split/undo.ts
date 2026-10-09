/**
 * Undo for split actions (FR-27): the last 10 actions of this session. Each action records the
 * before/after of every cell it touched; undoing sends the "before" states as set-state ticks.
 */
import type { TickChange } from "./coalesce";

export const UNDO_LIMIT = 10;

export type CellChange = { itemId: string; participant: string; before: boolean; after: boolean };

export type SplitAction = {
  kind: "tick" | "column" | "fill-rest" | "undo";
  changes: CellChange[];
};

/** Adds an action (no-op actions are dropped) and keeps only the newest `limit`. */
export function pushUndo(
  stack: readonly SplitAction[],
  action: SplitAction,
  limit = UNDO_LIMIT,
): SplitAction[] {
  const real = action.changes.filter((c) => c.before !== c.after);
  if (real.length === 0) return [...stack];
  return [...stack, { ...action, changes: real }].slice(-limit);
}

/** Removes the newest action and returns it with the remaining stack. */
export function popUndo(stack: readonly SplitAction[]): {
  action: SplitAction | undefined;
  stack: SplitAction[];
} {
  if (stack.length === 0) return { action: undefined, stack: [] };
  return { action: stack[stack.length - 1], stack: stack.slice(0, -1) };
}

/** The set-state writes that restore an action's "before" state. */
export function inverseChanges(action: SplitAction): TickChange[] {
  return action.changes.map((c) => ({ itemId: c.itemId, participant: c.participant, on: c.before }));
}
