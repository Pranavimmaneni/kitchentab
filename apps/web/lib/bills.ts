/**
 * Display helpers for bills. Nothing here is authoritative: shares shown after a server response
 * always come from the server (CLAUDE.md rule 5); `previewShares` only fills the gap while a tick
 * is in flight, mirroring the server's `bill_math.preview`.
 */
import { computeShares, weightToMilli, type Allocation, type BillInput } from "./money";
import { formatMoney } from "./format";
import { MEMBER_COLOR_COUNT } from "./members";
import { strings } from "./strings";
import type { BillItem, BillView, Participant, Reconciliation } from "./api/types";

/** Member colors in join order (apps/api MEMBER_COLORS, PRD §14). */
const MEMBER_HEX = [
  "#C6F432",
  "#6A4CFF",
  "#FF8A65",
  "#7FD3F7",
  "#F7D046",
  "#FF6FB5",
  "#4FD1A5",
  "#B0A6FF",
];

/** 0-based color slot for a member: from the server's hex color, else the sort order. */
export function colorIndexOf(p: { color: string; sort_order: number }): number {
  const index = MEMBER_HEX.indexOf(p.color.toUpperCase());
  return index >= 0
    ? index
    : ((p.sort_order % MEMBER_COLOR_COUNT) + MEMBER_COLOR_COUNT) % MEMBER_COLOR_COUNT;
}

/** "2026-10-02" → "10/02". */
export function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${m}/${d}`;
}

/** "2026-10-02" → "10/02/2026". */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${m}/${d}/${y}`;
}

/** Column tag: "YOU" for the signed-in member, else the first three letters. */
export function participantTag(
  p: Pick<Participant, "id" | "display_name">,
  meId: string | null,
): string {
  if (meId && p.id === meId) return strings.people.youTag;
  return p.display_name.replace(/\s+/g, "").slice(0, 3).toUpperCase();
}

export function displayName(
  p: Pick<Participant, "id" | "display_name">,
  meId: string | null,
): string {
  return meId && p.id === meId ? strings.people.you : p.display_name;
}

/** Parses "4.29", "$4.29", "4" into integer cents without floats; null when invalid. */
export function parseCents(raw: string, { allowNegative = false } = {}): number | null {
  const text = raw.trim().replace(/[$,\s]/g, "");
  const match = /^(-)?(\d{0,7})(?:\.(\d{0,2}))?$/.exec(text);
  if (!match || (match[2] === "" && (match[3] ?? "") === "")) return null;
  if (match[1] && !allowNegative) return null;
  const cents = Number(match[2] || "0") * 100 + Number((match[3] ?? "").padEnd(2, "0"));
  return match[1] ? -cents : cents;
}

/** Integer cents → "4.29" for an input field. */
export function centsToInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "";
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}${Math.trunc(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/** Sum of items + tax + fees − discounts, the way the server reconciles (adjustment excluded). */
export function computedTotal(
  items: readonly Pick<BillItem, "line_total_cents">[],
  taxCents: number,
  fees: readonly { kind: string; amount_cents: number }[],
): number {
  let total = taxCents;
  for (const item of items) total += item.line_total_cents;
  for (const fee of fees) {
    if (fee.kind === "adjustment") continue;
    total += fee.kind === "order_discount" ? -fee.amount_cents : fee.amount_cents;
  }
  return total;
}

export type ReconcileDisplay = { ok: boolean; text: string; differenceCents: number };

/** "*** MATHS CHECKS OUT ***" or "*** OFF BY $1.20 ***" (PRD §14A screen 2). */
export function reconcileDisplay(
  rec: Pick<Reconciliation, "receipt_total_cents" | "overridden">,
  computedCents: number,
  currency = "USD",
): ReconcileDisplay {
  const difference = rec.receipt_total_cents === null ? 0 : rec.receipt_total_cents - computedCents;
  if (difference === 0 || rec.overridden) {
    return { ok: true, text: strings.review.checksOut, differenceCents: difference };
  }
  return {
    ok: false,
    text: strings.review.offBy(formatMoney(Math.abs(difference), currency)),
    differenceCents: difference,
  };
}

/** Item id → participant keys on it, with optimistic overrides applied. */
export function assignedMap(
  items: readonly BillItem[],
  overrides: ReadonlyMap<string, boolean> = new Map(),
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const item of items) {
    const set = new Set(item.allocations.map((a) => a.participant));
    out[item.id] = [...set];
  }
  for (const [key, on] of overrides) {
    const [itemId, participant] = key.split("|") as [string, string];
    const list = out[itemId];
    if (!list) continue;
    const has = list.includes(participant);
    if (on && !has) list.push(participant);
    if (!on && has) out[itemId] = list.filter((p) => p !== participant);
  }
  return out;
}

/**
 * Instant, non-authoritative totals while a tick is in flight: only assigned items count and
 * bill-level amounts follow them (mirror of `bill_math.preview`). Weights default to 1; a custom
 * split's fixed share (ADR-0030) is kept.
 */
export function previewShares(
  view: BillView,
  assigned: Record<string, string[]>,
): Record<string, number> {
  const participants = view.participants.map((p) => ({ id: p.key, sortOrder: p.sort_order }));
  const zero = Object.fromEntries(participants.map((p) => [p.id, 0]));
  const known = new Set(participants.map((p) => p.id));
  const allocationOf = new Map<string, Allocation>();
  for (const item of view.items) {
    for (const a of item.allocations) {
      allocationOf.set(`${item.id}|${a.participant}`, {
        participant: a.participant,
        weightMilli: weightToMilli(a.weight),
        ...(a.share_num && a.share_den ? { share: [a.share_num, a.share_den] } : {}),
      });
    }
  }
  const assignedItems = view.items
    .map((item) => ({
      id: item.id,
      lineTotalCents: item.line_total_cents,
      allocations: (assigned[item.id] ?? [])
        .filter((p) => known.has(p))
        .map((p) => allocationOf.get(`${item.id}|${p}`) ?? { participant: p, weightMilli: 1000 }),
    }))
    .filter((item) => item.allocations.length > 0);
  if (assignedItems.length === 0) return zero;
  const fees: BillInput["fees"] = view.fees.map((f) => ({
    kind: f.kind,
    label: f.label,
    amountCents: f.amount_cents,
  }));
  let levels = view.bill.tax_cents;
  for (const f of fees) levels += f.kind === "order_discount" ? -f.amountCents : f.amountCents;
  const subtotal = assignedItems.reduce((sum, i) => sum + i.lineTotalCents, 0);
  try {
    const result = computeShares({
      participants,
      items: assignedItems,
      taxCents: view.bill.tax_cents,
      fees,
      totalCents: subtotal + levels,
    });
    return { ...zero, ...result.shares };
  } catch {
    return view.shares;
  }
}

/** Sub-line note on a split row: "$1.43 × 3" or "ALL MEERA" (display only). */
export function itemNote(
  item: Pick<BillItem, "line_total_cents"> & { allocations?: BillItem["allocations"] },
  on: readonly string[],
  nameOf: (key: string) => string,
  currency = "USD",
): string | undefined {
  if (on.length === 0) return undefined;
  if (on.length === 1) return strings.split.allOne(nameOf(on[0]!).toUpperCase());
  const custom = item.allocations?.find((a) => a.share_num && on.includes(a.participant));
  if (custom) return strings.split.customShare(nameOf(custom.participant).toUpperCase());
  const each = Math.trunc(item.line_total_cents / on.length);
  return `${formatMoney(each, currency)} × ${on.length}`;
}

/** Participants shown as split columns: everyone not excluded from this bill. */
export function splitColumns(view: BillView): Participant[] {
  return view.participants.filter(
    (p) =>
      !p.excluded &&
      (p.kind === "guest" ||
        p.status === "active" ||
        p.status === "placeholder" ||
        view.items.some((i) => i.allocations.some((a) => a.participant === p.key))),
  );
}

/** How many ways the bill is split: participants with at least one item. */
export function splitWays(view: Pick<BillView, "items">): number {
  const keys = new Set<string>();
  for (const item of view.items) for (const a of item.allocations) keys.add(a.participant);
  return keys.size;
}
