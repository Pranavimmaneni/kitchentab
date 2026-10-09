/**
 * The MONEY-03 corpus generator, line-for-line the same as `packages/fixtures/money_corpus.py`
 * (see its docstring for the spec). uint32 mulberry32, integer math only.
 */
import type { BillInput, FeeKind } from "./index";

export class Mulberry32 {
  private state: number;

  constructor(seed: number) {
    this.state = seed >>> 0;
  }

  nextU32(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0;
    let t = this.state;
    t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0;
    t = (t ^ ((t + (Math.imul(t ^ (t >>> 7), t | 61) >>> 0)) >>> 0)) >>> 0;
    return (t ^ (t >>> 14)) >>> 0;
  }

  r(lo: number, hi: number): number {
    return lo + (this.nextU32() % (hi - lo + 1));
  }
}

const FEE_KINDS: FeeKind[] = [
  "fee",
  "fee",
  "fee",
  "fee",
  "fee",
  "tip",
  "tip",
  "order_discount",
  "order_discount",
  "adjustment",
];

const pid = (n: number) => `p${String(n).padStart(2, "0")}`;

export function* generate(seed: number, count: number): Generator<BillInput> {
  const rng = new Mulberry32(seed);
  for (let b = 0; b < count; b++) {
    const n = rng.r(1, 20);
    const participants = Array.from({ length: n }, (_, j) => ({
      id: pid(j),
      sortOrder: rng.r(0, 3),
    }));
    const m = rng.r(0, 49) === 0 ? rng.r(1, 200) : rng.r(1, 40);
    const items = [];
    let total = 0;
    for (let k = 0; k < m; k++) {
      const kind = rng.r(0, 19);
      const cents = kind === 0 ? 0 : kind === 1 ? rng.r(-2000, -1) : rng.r(1, 50000);
      const c = rng.r(1, n);
      const idx = Array.from({ length: n }, (_, j) => j);
      for (let j = 0; j < c; j++) {
        const s = rng.r(j, n - 1);
        [idx[j], idx[s]] = [idx[s]!, idx[j]!];
      }
      const allocations = [];
      for (let j = 0; j < c; j++) {
        const weightMilli = rng.r(0, 4) ? 1000 : rng.r(1, 5000);
        allocations.push({ participant: pid(idx[j]!), weightMilli });
      }
      items.push({ id: `i${k}`, lineTotalCents: cents, allocations });
      total += cents;
    }
    const taxCents = rng.r(0, 5000);
    total += taxCents;
    const fees = [];
    const feeCount = rng.r(0, 3);
    for (let f = 0; f < feeCount; f++) {
      const kind = FEE_KINDS[rng.r(0, 9)]!;
      const amountCents = kind === "adjustment" ? rng.r(-500, 500) : rng.r(0, 3000);
      fees.push({ kind, label: `f${f}`, amountCents });
      total += kind === "order_discount" ? -amountCents : amountCents;
    }
    yield { participants, items, taxCents, fees, totalCents: total };
  }
}

/** Lines "i:p00=…,p01=…" in sorted id order, joined by "\n" (each line newline-terminated). */
export function digestLines(results: Iterable<Record<string, number>>): string {
  const lines: string[] = [];
  let i = 0;
  for (const shares of results) {
    const ids = Object.keys(shares).sort();
    lines.push(`${i}:${ids.map((id) => `${id}=${shares[id]}`).join(",")}\n`);
    i++;
  }
  return lines.join("");
}
