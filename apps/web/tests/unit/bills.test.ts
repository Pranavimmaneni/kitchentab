import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { BillView } from "../../lib/api/types";
import {
  assignedMap,
  centsToInput,
  computedTotal,
  itemNote,
  parseCents,
  participantTag,
  previewShares,
  reconcileDisplay,
} from "../../lib/bills";
import { ogCard } from "../../lib/og";
import { billView } from "./fixtures/bill";

const FIXTURES = join(__dirname, "..", "..", "..", "..", "packages", "fixtures");
type Case = {
  name: string;
  bill: {
    participants: { id: string; sort_order: number }[];
    items: { id: string; line_total_cents: number; allocations: { participant: string }[] }[];
    tax_cents: number;
    total_cents: number;
  };
  expected: { shares: Record<string, number> };
};
const shoprite = (JSON.parse(readFileSync(join(FIXTURES, "money_cases.json"), "utf8")) as Case[]).find(
  (c) => c.name === "shoprite_sent",
)!;

function shopriteView(): BillView {
  const base = billView();
  return billView({
    bill: { ...base.bill, tax_cents: shoprite.bill.tax_cents, total_cents: shoprite.bill.total_cents },
    items: shoprite.bill.items.map((item, position) => ({
      ...base.items[0]!,
      id: item.id,
      position,
      name: item.id,
      line_total_cents: item.line_total_cents,
      allocations: item.allocations.map((a) => ({ participant: `m:${a.participant}`, weight: "1.000" })),
    })),
    participants: shoprite.bill.participants.map((p) => ({
      ...base.participants[0]!,
      key: `m:${p.id}`,
      id: p.id,
      display_name: p.id,
      sort_order: p.sort_order,
    })),
    shares: {},
  });
}

describe("review reconciliation display", () => {
  it("shows *** MATHS CHECKS OUT *** when items + tax match the receipt total", () => {
    expect(reconcileDisplay({ receipt_total_cents: 9518, overridden: false }, 9518)).toEqual({
      ok: true,
      text: "*** MATHS CHECKS OUT ***",
      differenceCents: 0,
    });
  });

  it("shows *** OFF BY $X *** with the absolute difference when they don't", () => {
    expect(reconcileDisplay({ receipt_total_cents: 9518, overridden: false }, 9398).text).toBe(
      "*** OFF BY $1.20 ***",
    );
    expect(reconcileDisplay({ receipt_total_cents: 9398, overridden: false }, 9518)).toMatchObject({
      ok: false,
      text: "*** OFF BY $1.20 ***",
      differenceCents: -120,
    });
  });

  it("no printed total, or an override with a reason, counts as reconciled", () => {
    expect(reconcileDisplay({ receipt_total_cents: null, overridden: false }, 1234).ok).toBe(true);
    expect(reconcileDisplay({ receipt_total_cents: 9000, overridden: true }, 1234).ok).toBe(true);
  });

  it("computes items + tax + fees − discounts like the server, ignoring the adjustment line", () => {
    const items = [{ line_total_cents: 429 }, { line_total_cents: 549 }];
    const fees = [
      { kind: "fee", amount_cents: 10 },
      { kind: "order_discount", amount_cents: 100 },
      { kind: "adjustment", amount_cents: 999 },
    ];
    expect(computedTotal(items, 89, fees)).toBe(429 + 549 + 89 + 10 - 100);
  });
});

describe("money input", () => {
  it("parses dollars to integer cents without floats", () => {
    expect(parseCents("4.29")).toBe(429);
    expect(parseCents("$1,234.5")).toBe(123450);
    expect(parseCents("0.89")).toBe(89);
    expect(parseCents(".5")).toBe(50);
    expect(parseCents("7")).toBe(700);
    expect(parseCents("-1.20")).toBeNull();
    expect(parseCents("-1.20", { allowNegative: true })).toBe(-120);
    expect(parseCents("4.299")).toBeNull();
    expect(parseCents("abc")).toBeNull();
    expect(parseCents("")).toBeNull();
  });

  it("formats cents back for inputs", () => {
    expect(centsToInput(9518)).toBe("95.18");
    expect(centsToInput(5)).toBe("0.05");
    expect(centsToInput(-120)).toBe("-1.20");
    expect(centsToInput(null)).toBe("");
  });
});

describe("split display helpers", () => {
  it("live preview totals equal the ShopRite fixture once every item is assigned", () => {
    const view = shopriteView();
    const shares = previewShares(view, assignedMap(view.items));
    expect(shares).toEqual(
      Object.fromEntries(Object.entries(shoprite.expected.shares).map(([k, v]) => [`m:${k}`, v])),
    );
  });

  it("preview counts only assigned items, with tax following them", () => {
    const view = shopriteView();
    const assigned = assignedMap(view.items, new Map([["milk|m:pranav", false], ["milk|m:arjun", false], ["milk|m:meera", false]]));
    expect(assigned.milk).toEqual([]);
    const shares = previewShares(view, assigned);
    const sum = Object.values(shares).reduce((a, b) => a + b, 0);
    expect(sum).toBe(shoprite.bill.total_cents - 429);
  });

  it("row notes read '$1.43 × 3' or 'ALL MEERA'; tags read YOU or three letters", () => {
    expect(itemNote({ line_total_cents: 429 }, ["a", "b", "c"], () => "")).toBe("$1.43 × 3");
    expect(itemNote({ line_total_cents: 679 }, ["m:meera"], () => "Meera")).toBe("ALL MEERA");
    expect(itemNote({ line_total_cents: 399 }, [], () => "")).toBeUndefined();
    expect(participantTag({ id: "p", display_name: "Pranav" }, "p")).toBe("YOU");
    expect(participantTag({ id: "a", display_name: "Arjun" }, "p")).toBe("ARJ");
  });
});

describe("bill link preview", () => {
  it("LINK-05 the OG card shows store, date, total, SPLIT N WAYS and the house, never names or per-person amounts", () => {
    const view = shopriteView();
    view.participants = view.participants.map((p) => ({ ...p, display_name: `Roommate ${p.id}` }));
    const card = ogCard({ ...view, house: { name: "Apt 4B" } });
    expect(card).toMatchObject({
      store: "SHOPRITE",
      date: "10/02/2026",
      total: "$95.18",
      ways: "SPLIT 4 WAYS",
      house: "APT 4B",
    });
    const text = Object.values(card).join(" ");
    for (const p of view.participants) expect(text).not.toContain(p.display_name);
    for (const cents of Object.values(shoprite.expected.shares)) {
      expect(text).not.toContain(`$${centsToInput(cents)}`);
    }
  });
});
