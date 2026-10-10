/**
 * Display helpers for screen 5, the open bill (PRD §14A 5). Every amount here comes from the
 * server (shares, owed, payments); nothing is computed authoritatively (CLAUDE.md rule 5).
 */
import type { ActivityKind } from "@/components/ActivityTimeline";
import type { OwesStatus } from "@/components/WhoOwesRow";
import { isApiError } from "./api/client";
import type { ActivityEntry, BillMoney, Payment, PaymentHandles } from "./api/bill-life";
import type { BillView, Participant } from "./api/types";
import { centsToInput } from "./bills";
import { formatMoney } from "./format";
import { strings } from "./strings";

const DAY_MS = 24 * 60 * 60 * 1000;
export const REOPEN_WINDOW_MS = 30 * DAY_MS;
export const REMIND_EVERY_MS = 48 * 60 * 60 * 1000;

export type OwesRowModel = {
  key: string;
  participant: Participant;
  isMe: boolean;
  isPayer: boolean;
  shareCents: number;
  /** Server owed (share − confirmed payments); 0 for the payer. */
  owedCents: number;
  status: OwesStatus;
  statusAmountCents: number;
  /** Oldest payment waiting for the payer. */
  pending: Payment | null;
  pendingCents: number;
  confirmedCents: number;
};

/** Rows of the who-owes card: the payer first, then everyone with a share or a payment. */
export function owesRows(view: BillView, money: BillMoney | undefined): OwesRowModel[] {
  const payerKey = `m:${view.bill.payer_member_id}`;
  const meKey = view.me.member_id ? `m:${view.me.member_id}` : null;
  const payments = money?.payments ?? [];
  const rows: OwesRowModel[] = [];
  for (const p of view.participants) {
    const share = view.shares[p.key] ?? 0;
    const mine = payments.filter((x) => x.participant === p.key && x.direction === "to_payer");
    const pendingList = mine.filter((x) => x.status === "pending_confirmation");
    const confirmed = payments
      .filter((x) => x.participant === p.key && x.status === "confirmed")
      .reduce((sum, x) => sum + (x.direction === "to_payer" ? x.amount_cents : -x.amount_cents), 0);
    const isPayer = p.key === payerKey;
    const owed = isPayer ? 0 : (money?.owed[p.key] ?? share);
    if (!isPayer && share === 0 && mine.length === 0 && owed === 0) continue;
    const pendingCents = pendingList.reduce((sum, x) => sum + x.amount_cents, 0);
    let status: OwesStatus;
    let statusAmount = share;
    if (isPayer) status = "payer";
    else if (pendingList.length > 0) {
      status = "pending";
      statusAmount = pendingCents;
    } else if (owed > 0) status = "unpaid";
    else if (owed < 0) {
      status = "credit";
      statusAmount = -owed;
    } else {
      status = "paid";
      statusAmount = confirmed;
    }
    rows.push({
      key: p.key,
      participant: p,
      isMe: p.key === meKey,
      isPayer,
      shareCents: share,
      owedCents: owed,
      status,
      statusAmountCents: statusAmount,
      pending: pendingList[0] ?? null,
      pendingCents,
      confirmedCents: confirmed,
    });
  }
  return rows.sort((a, b) => Number(b.isPayer) - Number(a.isPayer));
}

/** "STILL OPEN · DAY {n}": day 1 is the day it was sent. */
export function dayOpen(sentAt: string | null, now: number): number {
  if (!sentAt) return 1;
  return Math.max(1, Math.floor((now - Date.parse(sentAt)) / DAY_MS) + 1);
}

export function canReopen(settledAt: string | null, now: number): boolean {
  return Boolean(settledAt) && now - Date.parse(settledAt!) <= REOPEN_WINDOW_MS;
}

/** "SUN 10/04 · 9:12 PM" in the viewer's own time zone (PRD §15 Time). */
export function formatWhen(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "2-digit",
    day: "2-digit",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone,
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday").toUpperCase()} ${get("month")}/${get("day")} · ${get("hour")}:${get("minute")} ${get(
    "dayPeriod",
  ).toUpperCase()}`;
}

/** "+$3.36" / "−$1.69" for the changed banner. */
export function signedMoney(cents: number, currency = "USD"): string {
  return `${cents < 0 ? "−" : "+"}${formatMoney(Math.abs(cents), currency)}`;
}

/** Was this member nudged in the last 48 h (from the loaded story)? Max one per 48 h (FR-41). */
export function remindedRecently(entries: readonly ActivityEntry[], memberId: string, now: number): boolean {
  return entries.some(
    (e) =>
      e.type === "reminder_sent" &&
      e.payload?.member_id === memberId &&
      now - Date.parse(e.created_at) < REMIND_EVERY_MS,
  );
}

export type ActivityLine = { id: string; kind: ActivityKind; text: string; when: string; dateTime: string };

type Names = { view: BillView; meName: string | null };

function participantName(key: unknown, { view }: Names, subject: boolean): string {
  const p = view.participants.find((x) => x.key === key);
  if (!p) return strings.openBill.activity.someone;
  if (view.me.member_id && p.id === view.me.member_id) {
    return subject ? strings.openBill.activity.you : strings.openBill.activity.you.toLowerCase();
  }
  return p.display_name;
}

/** "Meera's" / "your". */
function possessive(key: unknown, names: Names): string {
  const p = names.view.participants.find((x) => x.key === key);
  if (p && names.view.me.member_id && p.id === names.view.me.member_id) return strings.openBill.activity.your;
  return `${participantName(key, names, false)}'s`;
}

function itemName(id: unknown, { view }: Names): string | null {
  return view.items.find((i) => i.id === id)?.name ?? null;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function cents(value: unknown, currency: string): string {
  return typeof value === "number" && Number.isInteger(value) ? formatMoney(value, currency) : "";
}

const KIND: Record<string, ActivityKind> = {
  item_added: "edit",
  item_edited: "edit",
  item_deleted: "edit",
  items_merged: "edit",
  item_split: "edit",
  allocation_changed: "edit",
  bill_edited: "edit",
  reconcile_override: "edit",
  guest_added: "edit",
  ask_answered: "edit",
  payment_recorded: "payment",
  payment_confirmed: "payment",
  payment_rejected: "payment",
};

/** One line of "THE STORY SO FAR" from an activity row (FR-43). */
export function activityLine(entry: ActivityEntry, names: Names, timeZone?: string): ActivityLine {
  const A = strings.openBill.activity;
  const currency = names.view.bill.currency;
  const who = !entry.actor_name ? A.someone : entry.actor_name === names.meName ? A.you : entry.actor_name;
  const payload = entry.payload ?? {};
  let text: string;
  switch (entry.type) {
    case "bill_created":
      text = A.created(who);
      break;
    case "bill_sent":
      text = A.sent(who);
      break;
    case "bill_edited":
      text = A.edited(who);
      break;
    case "reconcile_override":
      text = A.override(who, str(payload.reason));
      break;
    case "item_added":
      text = A.itemAdded(who, str(payload.name));
      break;
    case "item_edited":
      text = A.itemEdited(who, str(payload.name));
      break;
    case "item_deleted":
      text = A.itemDeleted(who, str(payload.name));
      break;
    case "items_merged":
      text = A.merged(who, str(payload.name));
      break;
    case "item_split":
      text = A.itemSplit(who, str(payload.name));
      break;
    case "allocation_changed":
      text = ticksText(who, payload.changes, entry.actor_name, names);
      break;
    case "guest_added":
      text = A.guest(who, str(payload.name));
      break;
    case "payment_recorded": {
      const whom = participantName(payload.participant, names, true);
      text =
        payload.status === "confirmed"
          ? A.recorded(who, participantName(payload.participant, names, false), cents(payload.amount_cents, currency))
          : A.paid(whom, cents(payload.amount_cents, currency));
      break;
    }
    case "payment_confirmed":
      text = A.confirmedPay(who, possessive(payload.participant, names), cents(payload.amount_cents, currency));
      break;
    case "payment_rejected":
      text = A.rejectedPay(who, possessive(payload.participant, names), cents(payload.amount_cents, currency));
      break;
    case "bill_settled":
      text = A.settled(who);
      break;
    case "bill_reopened":
      text = A.reopened(who);
      break;
    case "bill_voided":
      text = A.voided(who, str(payload.reason));
      break;
    case "reminder_sent":
      text = A.reminded(who, participantName(`m:${str(payload.member_id)}`, names, false));
      break;
    default:
      text = A.other(who);
  }
  return {
    id: entry.id,
    kind: KIND[entry.type] ?? "system",
    text,
    when: formatWhen(entry.created_at, timeZone),
    dateTime: entry.created_at,
  };
}

function ticksText(who: string, raw: unknown, actorName: string | null, names: Names): string {
  const A = strings.openBill.activity;
  const changes = Array.isArray(raw)
    ? (raw as { item_id?: unknown; participant?: unknown; on?: unknown }[]).filter(
        (c) => typeof c.participant === "string" && typeof c.on === "boolean",
      )
    : [];
  if (changes.length === 0) return A.other(who);
  const actor = names.view.participants.find((p) => p.display_name === actorName && p.kind === "member");
  const self = actor !== undefined && changes.every((c) => c.participant === actor.key);
  if (self) {
    const on = changes.every((c) => c.on === true);
    const off = changes.every((c) => c.on === false);
    const items = changes.map((c) => itemName(c.item_id, names)).filter((n): n is string => Boolean(n));
    if ((on || off) && items.length > 0 && items.length <= 2) {
      const list = items.join(A.and);
      return on ? A.hoppedOn(who, list) : A.hoppedOff(who, list);
    }
    return A.ticks(who, changes.length);
  }
  if (changes.length === 1) {
    const c = changes[0]!;
    const item = itemName(c.item_id, names);
    if (item) {
      const whom = participantName(c.participant, names, false);
      return c.on ? A.putOn(who, whom, item) : A.movedOff(who, whom, item);
    }
  }
  return A.ticks(who, changes.length);
}

/** Pay-sheet deep links (PRD §8). Amounts as "33.91". */
export function payLinks(
  handles: PaymentHandles | undefined,
  amountCents: number,
  note: string,
): { method: "venmo" | "cashapp" | "paypal"; label: string; href: string; fallback?: string }[] {
  if (!handles) return [];
  const amount = centsToInput(Math.max(0, amountCents));
  const out: { method: "venmo" | "cashapp" | "paypal"; label: string; href: string; fallback?: string }[] = [];
  if (handles.venmo) {
    const user = encodeURIComponent(handles.venmo);
    const q = `txn=pay&amount=${amount}&note=${encodeURIComponent(note)}`;
    out.push({
      method: "venmo",
      label: "Venmo",
      href: `venmo://paycharge?txn=pay&recipients=${user}&amount=${amount}&note=${encodeURIComponent(note)}`,
      fallback: `https://venmo.com/${user}?${q}`,
    });
  }
  if (handles.cashapp) {
    out.push({
      method: "cashapp",
      label: "Cash App",
      href: `https://cash.app/$${encodeURIComponent(handles.cashapp.replace(/^\$/, ""))}/${amount}`,
    });
  }
  if (handles.paypal) {
    out.push({
      method: "paypal",
      label: "PayPal",
      href: `https://paypal.me/${encodeURIComponent(handles.paypal)}/${amount}`,
    });
  }
  return out;
}

/** "two minutes ago" for the possible-duplicate warning (SYNC-10). */
export function agoText(recordedAt: string, now: number): string {
  const seconds = Math.max(0, (now - Date.parse(recordedAt)) / 1000);
  if (seconds < 45) return strings.openBill.agoJustNow;
  if (seconds < 90) return strings.openBill.agoOneMinute;
  return strings.openBill.agoTwoMinutes;
}

/**
 * Copy for a failed write (PRD §21.3, §21.4): state codes get their own words, everything
 * else shows the server's title, and "no answer" is "Couldn't save".
 */
export function problemText(error: unknown): string {
  if (!isApiError(error)) return strings.error.couldntSave;
  switch (error.code) {
    case "BILL_SETTLED":
      return strings.sync.billSettled;
    case "BILL_VOIDED":
      return strings.sync.billVoided;
    case "LAST_PERSON":
      return strings.sync.lastPerson;
    case "ITEM_GONE":
      return strings.sync.itemGone;
    case "ALREADY_ANSWERED": {
      const payment = error.body.payment as { status?: string } | undefined;
      const how =
        payment?.status === "rejected" ? strings.sync.answeredRejected : strings.sync.answeredConfirmed;
      return strings.sync.alreadyAnswered(how);
    }
    default:
      return error.status === 0 ? strings.error.couldntSave : error.title;
  }
}
