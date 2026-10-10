/**
 * Display helpers for Tabs and Balances (PRD §14A 1, Balances). Every amount comes from the API
 * (balances are netted per pair on the server, FR-51); nothing here computes money.
 */
import type { BalanceLine } from "@/components/BalanceTicker";
import { formatMoney } from "./format";
import { strings } from "./strings";
import type { BalancePair, TabCard } from "./api/tabs-types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

/** "2026-10-02" → "Oct 2" (purchase dates are plain dates, PRD §15 Time). */
export function monDay(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${MONTHS[Number(m) - 1] ?? ""} ${Number(d)}`;
}

/** Local date → "OCT 09 2026" for the balance card. */
export function stampDate(date: Date): string {
  const month = (MONTHS[date.getMonth()] ?? "").toUpperCase();
  return `${month} ${String(date.getDate()).padStart(2, "0")} ${date.getFullYear()}`;
}

/** Signed money for the big balance: "+$40.37" or "−$14.10" (U+2212 minus). */
export function signedMoney(cents: number): string {
  if (cents === 0) return formatMoney(0);
  return `${cents > 0 ? "+" : "−"}${formatMoney(Math.abs(cents))}`;
}

export type Headline =
  | { kind: "owed"; text: string }
  | { kind: "owing"; text: string }
  | { kind: "square"; text: string };

/** Balance card headline (Main.dc.html): coming to you, you owe, or ALL SQUARE (BAL-04). */
export function balanceHeadline(netCents: number): Headline {
  if (netCents > 0) return { kind: "owed", text: signedMoney(netCents) };
  if (netCents < 0) return { kind: "owing", text: signedMoney(netCents) };
  return { kind: "square", text: strings.tabs.allSquare };
}

/** Pair rows for the ticker: "ARJUN → YOU $33.91" or "YOU → KABIR $1.68" (owing, warn). */
export function pairLines(pairs: readonly BalancePair[]): BalanceLine[] {
  return pairs
    .filter((p) => p.net_cents !== 0)
    .map((p) => ({
      id: p.member_id,
      from: p.net_cents > 0 ? p.display_name : strings.balance.you,
      to: p.net_cents > 0 ? strings.balance.you : p.display_name,
      amountCents: Math.abs(p.net_cents),
      kind: p.net_cents > 0 ? ("owed" as const) : ("owing" as const),
    }));
}

/** "Oct 2 · you paid · 12 items". */
export function cardMeta(card: TabCard, meMemberId: string | null): string {
  const payer =
    meMemberId && card.payer_member_id === meMemberId
      ? strings.tabs.you
      : (card.payer_name ?? strings.join.someone);
  return strings.tabs.meta(monDay(card.purchase_date), payer, card.item_count);
}

export type CardStamp = { text: string; tone: "lime" | "warn" | "muted" };

/**
 * The stamp on an open tab (Main.dc.html): "YOU OWE $14.10" (warn outline) when I still owe on
 * someone else's tab, else payment progress "1/3 PAID" (lime).
 */
export function tabStamp(card: TabCard, meMemberId: string | null): CardStamp | null {
  if (card.state === "settled") return { text: strings.tabs.settled, tone: "muted" };
  if (card.state === "voided") return { text: strings.tabs.voided, tone: "muted" };
  if (card.state !== "open") return null;
  const iPaid = meMemberId !== null && card.payer_member_id === meMemberId;
  const owed = card.my_owed_cents ?? 0;
  if (!iPaid && owed > 0)
    return { text: strings.tabs.youOweStamp(formatMoney(owed)), tone: "warn" };
  const debtors = card.debtor_count ?? 0;
  if (debtors === 0) return { text: strings.tabs.allPaid, tone: "lime" };
  return { text: strings.tabs.paid(card.paid_count ?? 0, debtors), tone: "lime" };
}

/** "today", "yesterday", a weekday within the last week, else "Oct 2". */
export function relativeDay(at: Date, now: Date): string {
  const day = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const diff = Math.round((day(now) - day(at)) / 86_400_000);
  if (diff <= 0) return strings.tabs.today;
  if (diff === 1) return strings.tabs.yesterday;
  if (diff < 7) return WEEKDAYS[at.getDay()] ?? "";
  return `${MONTHS[at.getMonth()]} ${at.getDate()}`;
}

/** "Meera tweaked it Sunday" when my share changed since I last looked (FR-35). */
export function changeNote(card: TabCard, now: Date): string | null {
  const changed = card.changed;
  if (!changed) return null;
  if (changed.by_name && changed.at) {
    return strings.tabs.tweaked(changed.by_name, relativeDay(new Date(changed.at), now));
  }
  return strings.tabs.shareChanged;
}

/** First name for "Hey, Pranav." */
export function firstName(displayName: string): string {
  return displayName.trim().split(/\s+/)[0] ?? displayName;
}
