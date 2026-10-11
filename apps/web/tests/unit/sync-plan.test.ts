import { describe, expect, it } from "vitest";
import type { BillView } from "../../lib/api/types";
import { planEvent, targetKey, type Plan } from "../../lib/sync/plan";
import type { HouseEvent } from "../../lib/sync/types";
import { billView } from "./fixtures/bill";

function event(partial: Partial<HouseEvent> & Pick<HouseEvent, "type" | "aggregate">): HouseEvent {
  return { seq: 1, actor_member_id: "arjun", client_mutation_id: null, data: null, ...partial };
}

const open = (overrides: Partial<BillView> = {}) =>
  billView({ bill: { ...billView().bill, state: "open", version: 4 }, ...overrides });

function run(plan: Plan, view: BillView): BillView {
  return plan.patches.reduce((v, p) => (p.billId === v.bill.id ? p.update(v) : v), view);
}

describe("event reducer (PRD §21.6 applying events)", () => {
  it("SYNC-01 a roommate's tick is patched straight into the cached grid with the server's shares [FR-32]", () => {
    const view = open();
    const plan = planEvent(
      event({
        type: "allocation_changed",
        aggregate: { type: "bill", id: "b1", version: 4 },
        data: {
          bill_id: "b1",
          changes: [{ item_id: "milk", participant: "m:arjun", on: true }],
          shares: { "m:pranav": 500, "m:arjun": 500 },
        },
      }),
      { myMemberId: "pranav", bills: () => [view] },
    );
    const next = run(plan, view);
    expect(next.items[0]!.allocations.map((a) => a.participant).sort()).toEqual([
      "m:arjun",
      "m:pranav",
    ]);
    expect(next.shares).toEqual({ "m:pranav": 500, "m:arjun": 500 });
    // Owed amounts and the story are server-computed: refetched, never derived here.
    expect(plan.invalidate.map(targetKey)).toEqual(["billLife:b1", "house"]);
  });

  it("SYNC-02 an untick that is already applied is a no-op patch (set-state, order-independent) [FR-33]", () => {
    const view = open();
    const plan = planEvent(
      event({
        type: "allocation_changed",
        aggregate: { type: "bill", id: "b1", version: 4 },
        data: {
          bill_id: "b1",
          changes: [{ item_id: "milk", participant: "m:arjun", on: false }],
          shares: { "m:pranav": 1000 },
        },
      }),
      { myMemberId: "pranav", bills: () => [view] },
    );
    expect(run(plan, view).items).toEqual(view.items);
  });

  it("SYNC-04 an item price edit at the next version patches the item and refetches the bill for new shares", () => {
    const view = open();
    const plan = planEvent(
      event({
        type: "item_updated",
        aggregate: { type: "item", id: "milk", version: 2 },
        data: {
          ...view.items[0]!,
          line_total_cents: 1200,
          version: 2,
          bill_id: "b1",
          share_deltas: {},
        },
      }),
      { myMemberId: "pranav", bills: () => [view] },
    );
    const next = run(plan, view);
    expect(next.items[0]!.line_total_cents).toBe(1200);
    expect(next.items[0]!.allocations).toEqual(view.items[0]!.allocations);
    expect(plan.invalidate.map(targetKey)).toContain("bill:b1");
  });

  it("SYNC-04 an event that skips a version is not patched: the bill is refetched instead", () => {
    const view = open();
    const plan = planEvent(
      event({
        type: "item_updated",
        aggregate: { type: "item", id: "milk", version: 5 },
        data: { ...view.items[0]!, line_total_cents: 999, version: 5, bill_id: "b1" },
      }),
      { myMemberId: "pranav", bills: () => [view] },
    );
    expect(plan.patches).toHaveLength(0);
    expect(plan.invalidate.map(targetKey)).toEqual(["bill:b1", "house"]);
  });

  it("SYNC-23 a deleted item disappears from the cached bill at once", () => {
    const view = open();
    const plan = planEvent(
      event({
        type: "item_deleted",
        aggregate: { type: "item", id: "milk", version: 2 },
        data: { item_id: "milk", bill_id: "b1" },
      }),
      { myMemberId: "pranav", bills: () => [view] },
    );
    const next = run(plan, view);
    expect(next.items).toHaveLength(0);
    expect(next.progress).toEqual({ assigned: 0, total: 0 });
  });

  it("SYNC-06 settle and reopen invalidate the bill (state, stamp and carryover come from a GET) [FR-39] [FR-40]", () => {
    const view = open();
    for (const type of ["bill_settled", "bill_reopened"]) {
      const plan = planEvent(
        event({ type, aggregate: { type: "bill", id: "b1", version: 5 }, data: null }),
        {
          myMemberId: "pranav",
          bills: () => [view],
        },
      );
      expect(plan.patches).toHaveLength(0);
      expect(plan.invalidate.map(targetKey)).toEqual(["bill:b1", "house"]);
    }
  });

  it("BILL-08 a void at the next version is patched with its reason [FR-42]", () => {
    const view = open();
    const plan = planEvent(
      event({
        type: "bill_voided",
        aggregate: { type: "bill", id: "b1", version: 5 },
        data: { reason: "Wrong receipt" },
      }),
      { myMemberId: "pranav", bills: () => [view] },
    );
    const next = run(plan, view);
    expect(next.bill).toMatchObject({ state: "voided", void_reason: "Wrong receipt", version: 5 });
  });

  it("PAY-01 payment events refetch money and activity of the house's bills (payments carry no bill id) [FR-36] [FR-37]", () => {
    const plan = planEvent(
      event({
        type: "payment_recorded",
        aggregate: { type: "payment", id: "p1", version: 1 },
        data: { participant: "m:arjun" },
      }),
      { myMemberId: "pranav", bills: () => [open()] },
    );
    expect(plan.patches).toHaveLength(0);
    expect(plan.invalidate.map(targetKey)).toEqual(["payments", "house"]);
  });

  it("SYNC-12 my own member being removed revokes; someone else's removal only refetches", () => {
    const mine = planEvent(
      event({ type: "member_removed", aggregate: { type: "member", id: "arjun", version: 3 } }),
      {
        myMemberId: "arjun",
        bills: () => [],
      },
    );
    expect(mine.revoked).toBe(true);
    const theirs = planEvent(
      event({ type: "member_removed", aggregate: { type: "member", id: "kabir", version: 3 } }),
      {
        myMemberId: "arjun",
        bills: () => [],
      },
    );
    expect(theirs.revoked).toBe(false);
    expect(theirs.invalidate.map(targetKey)).toEqual(["house", "bills", "me"]);
  });

  it("SYNC-14 kitchen and unknown events refetch what the house shows", () => {
    const plan = planEvent(
      event({ type: "inventory_changed", aggregate: { type: "inventory", id: "i1", version: 2 } }),
      {
        myMemberId: "pranav",
        bills: () => [],
      },
    );
    expect(plan.invalidate.map(targetKey)).toEqual(["house"]);
  });

  it("SYNC-G5 my own order changed on another device refetches; someone else's order changes nothing [FR-G02]", () => {
    const order = (member: string) =>
      event({
        type: "member.item_order.set",
        aggregate: { type: "item_order", id: member, version: null },
        data: { member_id: member, location: "fridge" },
      });
    const ctx = { myMemberId: "pranav", bills: () => [] };
    expect(planEvent(order("pranav"), ctx).invalidate.map(targetKey)).toEqual(["house"]);
    expect(planEvent(order("arjun"), ctx).invalidate).toEqual([]);
  });

  it("SYNC-G1 a move refetches what the house shows [FR-G03]", () => {
    const plan = planEvent(
      event({
        type: "items.moved",
        aggregate: { type: "inventory", id: "h1", version: null },
        data: {},
      }),
      { myMemberId: "pranav", bills: () => [] },
    );
    expect(plan.invalidate.map(targetKey)).toEqual(["house"]);
  });

  it("SYNC-23 an item event for a bill that is not cached refetches every bill of the house", () => {
    const plan = planEvent(
      event({ type: "item_added", aggregate: { type: "item", id: "new", version: 1 }, data: {} }),
      {
        myMemberId: "pranav",
        bills: () => [open()],
      },
    );
    expect(plan.invalidate.map(targetKey)).toEqual(["bills", "house"]);
  });
});
