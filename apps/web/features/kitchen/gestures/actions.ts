/**
 * What the three quick-action bubbles are for one item (SPEC §3, FR-G04): the two places the item
 * isn't in, then Used up (mine), Help yourself (open to me) or Ask [first owner].
 */
import type { InventoryItem, Place } from "@/lib/api/kitchen-types";

export const PLACES: readonly Place[] = ["freezer", "fridge", "pantry"];

export type BubbleAction =
  | { kind: "move"; to: Place }
  | { kind: "ask"; owner: string }
  | { kind: "usedUp" }
  | { kind: "help" }
  /** Already asked: the third bubble takes the ask back instead of asking twice. */
  | { kind: "cancelAsk"; requestId: string };

export function bubblesFor(item: InventoryItem): [BubbleAction, BubbleAction, BubbleAction] {
  const [a, b] = PLACES.filter((p) => p !== item.place) as [Place, Place];
  const third: BubbleAction = item.mine
    ? { kind: "usedUp" }
    : item.open_for_me || item.owners.length === 0
      ? { kind: "help" }
      : item.my_request
        ? { kind: "cancelAsk", requestId: item.my_request.id }
        : { kind: "ask", owner: item.owners[0]!.display_name };
  return [{ kind: "move", to: a }, { kind: "move", to: b }, third];
}

/** Select mode's bottom bar: every place except where all picked items already are (SPEC §4). */
export function destinations(picked: readonly Place[]): Place[] {
  return PLACES.filter((p) => !(picked.length > 0 && picked.every((q) => q === p)));
}

/** The move the screen would undo: back where each item came from, only if it's still there. */
export function inverseMoves(
  moved: ReadonlyArray<{ item_id: string; from: Place; to: Place }>,
): Array<{ item_id: string; to: Place; expect_from: Place }> {
  return moved.map((m) => ({ item_id: m.item_id, to: m.from, expect_from: m.to }));
}
