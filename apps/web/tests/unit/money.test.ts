import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  type BillInput,
  type RawBill,
  InvariantError,
  billFromRaw,
  computeShares,
  largestRemainder,
  weightToMilli,
} from "../../lib/money";
import { digestLines, generate } from "../../lib/money/corpus";

const FIXTURES = join(__dirname, "..", "..", "..", "..", "packages", "fixtures");
const cases = JSON.parse(readFileSync(join(FIXTURES, "money_cases.json"), "utf8")) as {
  name: string;
  bill: RawBill;
  expected: { shares: Record<string, number>; item_subtotals: Record<string, number> };
}[];

describe("compute_shares in TypeScript", () => {
  it.each(cases.map((c) => [c.name, c] as const))(
    "MONEY-02 fixture %s matches exactly [FR-23]",
    (_name, c) => {
      const result = computeShares(billFromRaw(c.bill));
      expect(result.shares).toEqual(c.expected.shares);
      expect(result.itemSubtotals).toEqual(c.expected.item_subtotals);
    },
  );

  it("MONEY-03 10,000-bill corpus digest equals the reference and Python [FR-23]", () => {
    const expected = JSON.parse(
      readFileSync(join(FIXTURES, "money_property_digest.json"), "utf8"),
    ) as {
      seed: number;
      count: number;
      sha256: string;
    };
    const results: Record<string, number>[] = [];
    for (const bill of generate(expected.seed, expected.count)) {
      const r = computeShares(bill);
      expect(Object.values(r.shares).reduce((a, b) => a + b, 0)).toBe(bill.totalCents);
      results.push(r.shares);
    }
    const sha = createHash("sha256").update(digestLines(results)).digest("hex");
    expect(sha).toBe(expected.sha256);
  }, 60_000);

  it("MONEY-03 invariant holds for random bills (fast-check)", () => {
    const bills: fc.Arbitrary<BillInput> = fc.integer({ min: 1, max: 20 }).chain((n) => {
      const ids = Array.from({ length: n }, (_, i) => `p${String(i).padStart(2, "0")}`);
      const item = fc.record({
        who: fc.uniqueArray(fc.constantFrom(...ids), { minLength: 1, maxLength: n }),
        cents: fc.integer({ min: -5000, max: 100_000 }),
        weight: fc.constantFrom(1000, 1000, 500, 2000, 1, 4999),
      });
      return fc
        .record({
          orders: fc.array(fc.integer({ min: 0, max: 3 }), { minLength: n, maxLength: n }),
          items: fc.array(item, { minLength: 1, maxLength: 30 }),
          tax: fc.integer({ min: 0, max: 10_000 }),
          discount: fc.integer({ min: 0, max: 5000 }),
          adjustment: fc.integer({ min: -999, max: 999 }),
        })
        .map(({ orders, items, tax, discount, adjustment }) => {
          const total = items.reduce((s, i) => s + i.cents, 0) + tax - discount + adjustment;
          return {
            participants: ids.map((id, i) => ({ id, sortOrder: orders[i]! })),
            items: items.map((i, k) => ({
              id: `i${k}`,
              lineTotalCents: i.cents,
              allocations: i.who.map((p) => ({ participant: p, weightMilli: i.weight })),
            })),
            taxCents: tax,
            fees: [
              { kind: "order_discount" as const, label: "c", amountCents: discount },
              { kind: "adjustment" as const, label: "a", amountCents: adjustment },
            ],
            totalCents: total,
          };
        });
    });
    fc.assert(
      fc.property(bills, (bill) => {
        const r = computeShares(bill);
        return Object.values(r.shares).reduce((a, b) => a + b, 0) === bill.totalCents;
      }),
      { numRuns: 2000 },
    );
  });

  it("MONEY-04 ties break by sort order then id, negatives mirror positives", () => {
    const order = new Map<string, [number, string]>([
      ["b", [0, "b"]],
      ["a", [1, "a"]],
      ["c", [1, "c"]],
    ]);
    const equal = new Map([
      ["a", 1],
      ["b", 1],
      ["c", 1],
    ]);
    expect(Object.fromEntries(largestRemainder(100, equal, order))).toEqual({
      a: 33,
      b: 34,
      c: 33,
    });
    expect(Object.fromEntries(largestRemainder(101, equal, order))).toEqual({
      a: 34,
      b: 34,
      c: 33,
    });
    expect(Object.fromEntries(largestRemainder(-101, equal, order))).toEqual({
      a: -34,
      b: -34,
      c: -33,
    });
  });

  it("parses weights exactly and refuses bad ones", () => {
    expect(weightToMilli("2.5")).toBe(2500);
    expect(weightToMilli(1)).toBe(1000);
    for (const bad of ["0", "-1", "1.0005", "x"]) expect(() => weightToMilli(bad)).toThrow();
  });

  it("refuses rather than adjusts when the invariant could break", () => {
    expect(() =>
      computeShares({
        participants: [{ id: "a", sortOrder: 0 }],
        items: [],
        taxCents: 50,
        fees: [],
        totalCents: 50,
      }),
    ).toThrow(/no allocated participants/);
    expect(InvariantError.prototype).toBeInstanceOf(Error);
  });

  it("MONEY-06 no float arithmetic in the money code", () => {
    const dir = join(__dirname, "..", "..", "lib", "money");
    for (const file of readdirSync(dir)) {
      const src = readFileSync(join(dir, file), "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/.*$/gm, "");
      expect(src, file).not.toMatch(
        /parseFloat|toFixed|Math\.round|Math\.floor|Math\.ceil|\b\d+\.\d+\b/,
      );
      expect(src, file).not.toMatch(/\/\s*100\b/);
    }
  });
});
