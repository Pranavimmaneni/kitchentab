/**
 * The event reducer (PRD §21.6 "Applying events"), kept pure so it can be tested on its own:
 * given one house event and what the cache holds, decide which cached aggregates to patch in
 * place and which to invalidate (refetch, debounced 150 ms and coalesced per target).
 *
 * - Patch only when the event carries the delta and the cache is at the version right before
 *   it (`aggregate.version == cached.version + 1`), or for set-state ticks, which never bump a
 *   version (SYNC-04). Everything else is invalidated and refetched with a plain GET.
 * - Money is never computed here: shares come from the event (server-computed) and owed amounts,
 *   balances and activity are always refetched (CLAUDE.md rule 5).
 */
import type { BillItem, BillView } from "../api/types";
import { applyChanges } from "../split/useSplitWrites";
import type { HouseEvent } from "./types";

export type Target =
  /** `["bill", id]` and everything under it (money, activity). */
  | { kind: "bill"; id: string }
  /** Only `["bill", id, "money"]` and `["bill", id, "activity"]`. */
  | { kind: "billLife"; id: string }
  /** Every cached bill of this house (an item event without a known bill). */
  | { kind: "bills" }
  /** Money and activity of every cached bill of this house (payments carry no bill id). */
  | { kind: "payments" }
  /** Every non-bill query keyed by this house id: house, bill lists, kitchen, balances… */
  | { kind: "house" }
  | { kind: "me" };

export type Patch = { billId: string; update: (view: BillView) => BillView };

export type Plan = { patches: Patch[]; invalidate: Target[]; revoked: boolean };

export type PlanContext = {
  /** The signed-in member in this house. */
  myMemberId: string | null;
  /** Cached bill views (only bills of this house matter). */
  bills: () => readonly BillView[];
};

type Change = { item_id: string; participant: string; on: boolean };

function isChangeList(value: unknown): value is Change[] {
  return (
    Array.isArray(value) &&
    value.every(
      (c) =>
        typeof c === "object" &&
        c !== null &&
        typeof (c as Change).item_id === "string" &&
        typeof (c as Change).participant === "string" &&
        typeof (c as Change).on === "boolean",
    )
  );
}

function isShares(value: unknown): value is Record<string, number> {
  return (
    typeof value === "object" &&
    value !== null &&
    Object.values(value).every((v) => typeof v === "number" && Number.isInteger(v))
  );
}

const REVOKING = new Set(["member_removed", "member_left", "member_unclaimed"]);

/** Fields of an `item_updated` event that belong on the cached item. */
function itemFromEvent(data: Record<string, unknown>, old: BillItem): BillItem {
  const next = { ...old } as Record<string, unknown>;
  for (const key of Object.keys(old)) {
    if (key in data) next[key] = data[key];
  }
  return next as BillItem;
}

export function planEvent(event: HouseEvent, ctx: PlanContext): Plan {
  const plan: Plan = { patches: [], invalidate: [], revoked: false };
  const { aggregate, data } = event;
  const bills = ctx.bills();

  switch (aggregate.type) {
    case "bill": {
      const id = aggregate.id;
      const cached = bills.find((b) => b.bill.id === id);
      if (event.type === "allocation_changed" && cached && data && isChangeList(data.changes)) {
        const changes = data.changes.map((c) => ({
          itemId: c.item_id,
          participant: c.participant,
          on: c.on,
        }));
        const shares = isShares(data.shares) ? data.shares : null;
        plan.patches.push({
          billId: id,
          update: (view) => {
            const next = applyChanges(view, changes);
            return shares ? { ...next, shares } : next;
          },
        });
        // Owed amounts and the story follow the new shares; both come from the server.
        plan.invalidate.push({ kind: "billLife", id }, { kind: "house" });
        if (!shares) plan.invalidate.push({ kind: "bill", id });
        break;
      }
      if (
        event.type === "bill_voided" &&
        cached &&
        aggregate.version !== null &&
        aggregate.version === cached.bill.version + 1 &&
        data &&
        typeof data.reason === "string"
      ) {
        const reason = data.reason;
        const version = aggregate.version;
        plan.patches.push({
          billId: id,
          update: (view) => ({
            ...view,
            bill: { ...view.bill, state: "voided", void_reason: reason, version },
          }),
        });
        plan.invalidate.push({ kind: "billLife", id }, { kind: "house" });
        break;
      }
      plan.invalidate.push({ kind: "bill", id }, { kind: "house" });
      break;
    }
    case "item": {
      const itemId = aggregate.id;
      const hinted = data && typeof data.bill_id === "string" ? data.bill_id : null;
      const owner =
        bills.find((b) => b.items.some((i) => i.id === itemId)) ??
        (hinted ? bills.find((b) => b.bill.id === hinted) : undefined);
      const billId = owner?.bill.id ?? hinted;
      if (event.type === "item_deleted" && owner) {
        // SYNC-23: the row disappears on every device right away; shares are refetched.
        plan.patches.push({
          billId: owner.bill.id,
          update: (view) => {
            const items = view.items.filter((i) => i.id !== itemId);
            const assigned = items.filter((i) => i.allocations.length > 0).length;
            return { ...view, items, progress: { assigned, total: items.length } };
          },
        });
      } else if (event.type === "item_updated" && owner && data && aggregate.version !== null) {
        const cachedItem = owner.items.find((i) => i.id === itemId);
        if (cachedItem && aggregate.version === cachedItem.version + 1) {
          plan.patches.push({
            billId: owner.bill.id,
            update: (view) => ({
              ...view,
              items: view.items.map((i) => (i.id === itemId ? itemFromEvent(data, i) : i)),
            }),
          });
        }
      }
      // A price or line change moves shares and totals: always refetch the bill.
      plan.invalidate.push(billId ? { kind: "bill", id: billId } : { kind: "bills" }, {
        kind: "house",
      });
      break;
    }
    case "payment":
      plan.invalidate.push({ kind: "payments" }, { kind: "house" });
      break;
    case "member":
      if (REVOKING.has(event.type) && ctx.myMemberId && aggregate.id === ctx.myMemberId) {
        plan.revoked = true;
      }
      plan.invalidate.push({ kind: "house" }, { kind: "bills" }, { kind: "me" });
      break;
    case "house":
      plan.invalidate.push({ kind: "house" }, { kind: "me" });
      break;
    default:
      // Kitchen, asks and anything newer than this client: refetch what the house shows.
      plan.invalidate.push({ kind: "house" });
  }
  return plan;
}

/** Stable key for coalescing invalidations of the same target. */
export function targetKey(target: Target): string {
  return "id" in target ? `${target.kind}:${target.id}` : target.kind;
}
