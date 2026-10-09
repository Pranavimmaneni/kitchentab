import { describe, expect, it } from "vitest";
import { UNDO_LIMIT, inverseChanges, popUndo, pushUndo, type SplitAction } from "../../lib/split/undo";

type Grid = Map<string, boolean>;

function apply(grid: Grid, changes: { itemId: string; participant: string; on: boolean }[]): Grid {
  const next = new Map(grid);
  for (const c of changes) next.set(`${c.itemId}|${c.participant}`, c.on);
  return next;
}

const onKeys = (grid: Grid) => [...grid].filter(([, on]) => on).map(([k]) => k);

describe("split undo", () => {
  it("SPLIT-04 keeps the last 10 actions and undoing them restores the state before each [FR-27]", () => {
    let grid: Grid = new Map();
    let stack: SplitAction[] = [];
    const history: Grid[] = [grid];
    for (let i = 0; i < 12; i++) {
      const itemId = `item-${i}`;
      const action: SplitAction = {
        kind: i % 3 === 0 ? "column" : "tick",
        changes: [
          { itemId, participant: "m:a", before: false, after: true },
          { itemId, participant: "m:b", before: false, after: true },
        ],
      };
      grid = apply(
        grid,
        action.changes.map((c) => ({ itemId: c.itemId, participant: c.participant, on: c.after })),
      );
      stack = pushUndo(stack, action);
      history.push(grid);
    }
    expect(stack).toHaveLength(UNDO_LIMIT);

    for (let n = 12; n > 2; n--) {
      const popped = popUndo(stack);
      expect(popped.action).toBeDefined();
      grid = apply(grid, inverseChanges(popped.action!));
      stack = popped.stack;
      expect(onKeys(grid)).toEqual(onKeys(history[n - 1]!));
    }
    // Only 10 are remembered: the two oldest actions stay applied.
    expect(popUndo(stack).action).toBeUndefined();
    expect(onKeys(grid)).toEqual([
      "item-0|m:a",
      "item-0|m:b",
      "item-1|m:a",
      "item-1|m:b",
    ]);
  });

  it("drops changes that did nothing and ignores empty actions", () => {
    const stack = pushUndo([], {
      kind: "column",
      changes: [
        { itemId: "a", participant: "m:x", before: true, after: true },
        { itemId: "b", participant: "m:x", before: false, after: true },
      ],
    });
    expect(stack[0]!.changes).toHaveLength(1);
    expect(pushUndo(stack, { kind: "tick", changes: [] })).toHaveLength(1);
  });

  it("the inverse of an action sets every cell back to its before state", () => {
    expect(
      inverseChanges({
        kind: "fill-rest",
        changes: [{ itemId: "a", participant: "m:x", before: false, after: true }],
      }),
    ).toEqual([{ itemId: "a", participant: "m:x", on: false }]);
  });
});
