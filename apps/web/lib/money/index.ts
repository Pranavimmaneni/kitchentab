/**
 * Split math (PRD §6), TypeScript mirror of `apps/api/app/domain/money.py`.
 *
 * Used only for instant, non-authoritative UI totals while splitting (CLAUDE.md rule 5); the
 * server's result always wins. Integer cents throughout; the products in largest-remainder use
 * BigInt so nothing is ever rounded. Must match `packages/fixtures/money_cases.json` and the
 * 10,000-bill corpus digest exactly (MONEY-02, MONEY-03).
 */

export type FeeKind = "fee" | "tip" | "order_discount" | "adjustment";

export interface Participant {
  id: string;
  sortOrder: number;
}

export interface Allocation {
  participant: string;
  /** Weight in thousandths (numeric(6,3) on the server); 1000 = weight 1. */
  weightMilli: number;
}

export interface Item {
  id: string;
  lineTotalCents: number;
  allocations: Allocation[];
}

export interface Fee {
  kind: FeeKind;
  label: string;
  /** Positive except `adjustment`, which is signed (ADR-0002). */
  amountCents: number;
}

export interface BillInput {
  participants: Participant[];
  items: Item[];
  taxCents: number;
  fees: Fee[];
  totalCents: number;
}

export interface ShareResult {
  itemSubtotals: Record<string, number>;
  shares: Record<string, number>;
}

export class MoneyError extends Error {}
export class ReconciliationError extends MoneyError {}
export class InvariantError extends MoneyError {}

type Order = Map<string, [number, string]>;

function compareOrder(a: [number, string], b: [number, string]): number {
  if (a[0] !== b[0]) return a[0] - b[0];
  return a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0;
}

export function weightToMilli(weight: number | string): number {
  const text = String(weight);
  const match = /^(\d+)(?:\.(\d{1,3}))?$/.exec(text);
  if (!match) throw new MoneyError(`weight must be positive with at most 3 decimals: ${text}`);
  const milli = Number(match[1]) * 1000 + Number((match[2] ?? "").padEnd(3, "0"));
  if (milli <= 0) throw new MoneyError(`weight must be positive: ${text}`);
  return milli;
}

/** Split integer `amount` across positive integer `weights` exactly (largest remainder). */
export function largestRemainder(
  amount: number,
  weights: Map<string, number>,
  order: Order,
): Map<string, number> {
  const out = new Map<string, number>();
  if (weights.size === 0) return out;
  if (amount === 0) {
    for (const key of weights.keys()) out.set(key, 0);
    return out;
  }
  let totalWeight = 0n;
  for (const w of weights.values()) {
    if (w <= 0) throw new MoneyError("weights must be positive");
    totalWeight += BigInt(w);
  }
  const sign = amount < 0 ? -1 : 1;
  const magnitude = BigInt(Math.abs(amount));
  const remainders = new Map<string, bigint>();
  let floorSum = 0n;
  for (const [key, w] of weights) {
    const product = magnitude * BigInt(w);
    const floor = product / totalWeight;
    remainders.set(key, product % totalWeight);
    out.set(key, Number(floor));
    floorSum += floor;
  }
  let left = Number(magnitude - floorSum);
  const ranked = [...weights.keys()].sort((a, b) => {
    const ra = remainders.get(a)!;
    const rb = remainders.get(b)!;
    if (ra !== rb) return ra > rb ? -1 : 1;
    return compareOrder(order.get(a)!, order.get(b)!);
  });
  for (const key of ranked) {
    if (left === 0) break;
    out.set(key, out.get(key)! + 1);
    left -= 1;
  }
  for (const [key, value] of out) out.set(key, sign * value);
  return out;
}

export function billLevelAmounts(bill: Pick<BillInput, "taxCents" | "fees">): number[] {
  return [
    bill.taxCents,
    ...bill.fees.map((f) => (f.kind === "order_discount" ? -f.amountCents : f.amountCents)),
  ];
}

export function computeShares(bill: BillInput): ShareResult {
  const order: Order = new Map(bill.participants.map((p) => [p.id, [p.sortOrder, p.id]]));
  const itemSub = new Map<string, number>(bill.participants.map((p) => [p.id, 0]));
  const allocatedAny: string[] = [];
  const seen = new Set<string>();

  for (const item of bill.items) {
    if (item.allocations.length === 0) throw new MoneyError(`item ${item.id} has no allocations`);
    const weights = new Map<string, number>();
    for (const a of item.allocations) {
      if (!order.has(a.participant)) {
        throw new MoneyError(`item ${item.id} allocated to unknown participant ${a.participant}`);
      }
      if (weights.has(a.participant))
        throw new MoneyError(`item ${item.id} allocates ${a.participant} twice`);
      weights.set(a.participant, a.weightMilli);
    }
    for (const [pid, cents] of largestRemainder(item.lineTotalCents, weights, order)) {
      itemSub.set(pid, itemSub.get(pid)! + cents);
      if (!seen.has(pid)) {
        seen.add(pid);
        allocatedAny.push(pid);
      }
    }
  }

  let clipped = new Map(allocatedAny.map((pid) => [pid, Math.max(itemSub.get(pid)!, 0)]));
  if ([...clipped.values()].every((v) => v === 0))
    clipped = new Map(allocatedAny.map((pid) => [pid, 1]));
  const positive = new Map([...clipped].filter(([, w]) => w > 0));

  const shares = new Map(itemSub);
  const levels = billLevelAmounts(bill);
  for (const amount of levels) {
    if (amount !== 0 && positive.size === 0)
      throw new MoneyError("bill-level amounts but no allocated participants");
    for (const [pid, cents] of largestRemainder(amount, positive, order))
      shares.set(pid, shares.get(pid)! + cents);
  }

  const expectedTotal =
    bill.items.reduce((sum, i) => sum + i.lineTotalCents, 0) +
    levels.reduce((sum, a) => sum + a, 0);
  if (expectedTotal !== bill.totalCents) {
    throw new ReconciliationError(
      `bill does not reconcile: computed ${expectedTotal} vs total ${bill.totalCents}`,
    );
  }
  let sum = 0;
  for (const v of shares.values()) sum += v;
  if (sum !== bill.totalCents) throw new InvariantError("share invariant violated");
  return { itemSubtotals: Object.fromEntries(itemSub), shares: Object.fromEntries(shares) };
}

/** The fixture / reference JSON shape (`packages/fixtures/money_cases.json`). */
export interface RawBill {
  participants: { id: string; sort_order: number }[];
  items: {
    id: string;
    line_total_cents: number;
    allocations: { participant: string; weight?: number | string }[];
  }[];
  tax_cents?: number;
  fees?: { kind: FeeKind; label: string; amount_cents: number }[];
  total_cents: number;
}

export function billFromRaw(raw: RawBill): BillInput {
  return {
    participants: raw.participants.map((p) => ({ id: p.id, sortOrder: p.sort_order })),
    items: raw.items.map((i) => ({
      id: i.id,
      lineTotalCents: i.line_total_cents,
      allocations: i.allocations.map((a) => ({
        participant: a.participant,
        weightMilli: weightToMilli(a.weight ?? 1),
      })),
    })),
    taxCents: raw.tax_cents ?? 0,
    fees: (raw.fees ?? []).map((f) => ({
      kind: f.kind,
      label: f.label,
      amountCents: f.amount_cents,
    })),
    totalCents: raw.total_cents,
  };
}
