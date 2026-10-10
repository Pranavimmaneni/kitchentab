/**
 * Display logic for the fridge, pantry and requests (PRD §19.3-§19.8). Ownership, sharing state,
 * use-soon flags, filter counts and share deltas all come from the API; these helpers only pick
 * words, labels and which controls to show (CLAUDE.md rule 5).
 */
import { formatMoney } from "./format";
import { parseCents } from "./bills";
import { strings } from "./strings";
import type {
  InventoryItem,
  KitchenFilter,
  RequestStatus,
  Resolution,
  ShareRequest,
} from "./api/kitchen-types";

export const FILTERS: readonly KitchenFilter[] = ["all", "mine", "open", "use_soon"];
export const FILTER_STORAGE_KEY = "kt.kitchen.filter";

/** Mirrors the server's filter keys (routes/kitchen.py): OPEN means open for me. */
export function matchesFilter(item: InventoryItem, filter: KitchenFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "mine":
      return item.mine;
    case "open":
      return item.open_for_me;
    case "use_soon":
      return item.use_soon;
  }
}

export function isFilter(value: unknown): value is KitchenFilter {
  return typeof value === "string" && (FILTERS as readonly string[]).includes(value);
}

/** "Pranav", "Pranav & Arjun", "Pranav, Arjun & Meera" (Fridge.dc.html `names`). */
export function joinNames(names: readonly string[]): string {
  if (names.length === 0) return "";
  if (names.length === 1) return names[0]!;
  return `${names.slice(0, -1).join(", ")} ${strings.place.and} ${names[names.length - 1]}`;
}

export function ownerNames(
  item: InventoryItem,
  meMemberId: string | null,
  you: string = strings.place.you,
): string[] {
  return item.owners.map((o) => (o.id === meMemberId ? you : o.display_name));
}

/** Badge on the tile: lime check when the viewer may help themselves, else a lock. */
export function showsOpenBadge(item: InventoryItem): boolean {
  return item.is_open || item.granted_to_me;
}

/** Screen-reader label for a tile (FR-67): name, owners, share state, use soon. */
export function tileLabel(item: InventoryItem, meMemberId: string | null): string {
  const owners =
    joinNames(ownerNames(item, meMemberId, strings.place.youLower)) || strings.place.noOwners;
  return strings.place.tileLabel(item.name, owners, showsOpenBadge(item), item.use_soon);
}

export type SheetMode = "owner" | "open" | "asked" | "locked";

/** Which action set the item sheet shows for the viewer (FR-68, PRD §19.8 a–d). */
export function sheetMode(item: InventoryItem): SheetMode {
  if (item.mine) return "owner";
  if (item.open_for_me) return "open";
  if (item.my_request) return "asked";
  return "locked";
}

/** Owner view: what my switch means given the other owners (FR-69 "names owners holding out"). */
export function coOwnerNote(item: InventoryItem, meMemberId: string | null): string {
  const me = item.owners.find((o) => o.id === meMemberId);
  if (!me?.open_to_share) return strings.item.coOff;
  const others = item.owners.filter((o) => o.id !== meMemberId);
  const holdouts = others.filter((o) => !o.open_to_share).map((o) => o.display_name);
  if (holdouts.length) return strings.item.coHoldout(joinNames(holdouts), holdouts.length > 1);
  return others.length ? strings.item.coAllOpen : strings.item.coSoloOpen;
}

/** Open for the viewer: who is happy to share, or who approved the ask. */
export function helpNote(item: InventoryItem): string {
  if (!item.is_open && item.granted_to_me) {
    return item.granted_by ? strings.item.approvedBy(item.granted_by) : strings.item.approved;
  }
  const names = item.owners.map((o) => o.display_name);
  return strings.item.helpNote(joinNames(names), names.length > 1);
}

export function askNote(item: InventoryItem): string {
  const names = item.owners.map((o) => o.display_name);
  return names.length > 1
    ? strings.item.askNoteMany(joinNames(names))
    : strings.item.askNoteOne(names[0] ?? "");
}

export function askLabel(item: InventoryItem): string {
  return strings.item.ask(joinNames(item.owners.map((o) => o.display_name)));
}

export function askedLabel(item: InventoryItem): string {
  const names = item.owners.map((o) => o.display_name);
  return strings.item.asked(names.length === 1 ? names[0]! : strings.item.anOwner);
}

/** "1 DAY LEFT" etc. come from the server (`soon_label`); this is the fallback wording. */
export function soonText(item: InventoryItem): string {
  return item.soon_label ?? strings.place.soonTag;
}

export const NOTE_MAX = 140;

/** Months for "OCT 2" in source lines. */
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** "2026-10-02" → "OCT 2". */
export function monDayUpper(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${MONTHS[Number(m) - 1] ?? ""} ${Number(d)}`;
}

/** "SHOPRITE · OCT 2 · $11.37", or "LEFTOVERS · ADDED OCT 6" for manual items. */
export function sourceLine(item: InventoryItem): string {
  if (item.source) {
    return strings.item.source(
      item.source.store_name ?? strings.bill.untitled,
      monDayUpper(item.source.purchase_date),
      item.price_cents === null ? "" : formatMoney(item.price_cents),
    );
  }
  return strings.item.leftovers(monDayUpper(item.stocked_on));
}

// ---- Requests (K4) -----------------------------------------------------------------------

/** "12 MIN AGO", "1 HOUR AGO", "2 DAYS AGO". */
export function ageLabel(createdAt: string, now: Date): string {
  const minutes = Math.floor((now.getTime() - new Date(createdAt).getTime()) / 60_000);
  if (minutes < 1) return strings.requests.justNow;
  if (minutes < 60) return strings.requests.minAgo(minutes);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return strings.requests.hoursAgo(hours);
  return strings.requests.daysAgo(Math.floor(hours / 24));
}

/** "5 MIN" for the waiting stamp. */
export function waitingFor(createdAt: string, now: Date): string {
  return ageLabel(createdAt, now).replace(/ AGO$/, "");
}

export function coOwnersLine(coOwners: readonly string[]): string {
  if (coOwners.length === 0) return strings.requests.coOwnersNone;
  if (coOwners.length === 1) return strings.requests.coOwnersOne(coOwners[0]!);
  return strings.requests.coOwnersMany(joinNames(coOwners));
}

/** Signed delta chip text: "KABIR +$3.83", "YOU −$1.92" (U+2212). */
export function deltaChip(name: string, cents: number): string {
  const sign = cents > 0 ? "+" : "−";
  return `${name.toUpperCase()} ${sign}${formatMoney(Math.abs(cents))}`;
}

/**
 * The exact deltas the server previewed for "Add them to the split" (FR-72), keyed `m:{id}`.
 * Gains first, then the biggest drops; names from the house members ("YOU" for me).
 */
export function splitDeltaChips(
  deltas: Record<string, number>,
  names: Record<string, string>,
  meMemberId: string | null,
): string[] {
  return Object.entries(deltas)
    .filter(([, cents]) => cents !== 0)
    .sort(([ka, a], [kb, b]) =>
      a > 0 !== b > 0 ? (a > 0 ? -1 : 1) : Math.abs(b) - Math.abs(a) || ka.localeCompare(kb),
    )
    .map(([key, cents]) => {
      const id = key.replace(/^[mg]:/, "");
      const name =
        id === meMemberId ? strings.people.youTag : (names[id] ?? strings.requests.someone);
      return deltaChip(name, cents);
    });
}

/** Chip-in amount in cents when it is within $0.01..line total, else null (FR-72, ASK-05). */
export function chipInCents(raw: string, lineTotalCents: number): number | null {
  const cents = parseCents(raw);
  if (cents === null || cents < 1 || cents > lineTotalCents) return null;
  return cents;
}

export type Stamp = { text: string; tone: "lime" | "paper" | "violet" };

/** The stamp on an answered or closed ask (Requests.dc.html). */
export function requestStamp(
  req: Pick<ShareRequest, "status" | "resolution" | "chip_in_cents" | "created_at">,
  now: Date,
): Stamp {
  switch (req.status as RequestStatus) {
    case "pending":
      return {
        text: strings.requests.stamps.waiting(waitingFor(req.created_at, now)),
        tone: "paper",
      };
    case "declined":
      return { text: strings.requests.stamps.declined, tone: "paper" };
    case "expired":
      return { text: strings.requests.stamps.expired, tone: "paper" };
    case "cancelled":
      return { text: strings.requests.stamps.cancelled, tone: "paper" };
    case "approved": {
      const resolution = req.resolution as Resolution | null;
      if (resolution === "split") return { text: strings.requests.stamps.split, tone: "lime" };
      if (resolution === "chip_in")
        return {
          text: strings.requests.stamps.chipIn(formatMoney(req.chip_in_cents ?? 0)),
          tone: "lime",
        };
      return { text: strings.requests.stamps.free, tone: "lime" };
    }
  }
}

/** One line under an answered incoming ask. */
export function doneNote(req: ShareRequest, meMemberId: string | null): string {
  const who = req.requester.display_name ?? strings.requests.someone;
  const store = req.source?.store_name ?? strings.bill.untitled;
  let line: string;
  if (req.status === "declined") line = strings.requests.done.declined(who);
  else if (req.status === "expired") line = strings.requests.done.expired;
  else if (req.status === "cancelled") line = strings.requests.done.cancelled;
  else if (req.resolution === "split") line = strings.requests.done.split(who, store);
  else if (req.resolution === "chip_in")
    line = strings.requests.done.chipIn(who, formatMoney(req.chip_in_cents ?? 0));
  else line = strings.requests.done.free(who);
  const by = req.answered_by;
  if (by && by.id !== meMemberId && by.display_name)
    return `${strings.requests.done.by(by.display_name)} ${line}`;
  return line;
}

/** Owners may undo an answer for 5 minutes (FR-76); the server enforces the window. */
export function canUndo(req: Pick<ShareRequest, "status" | "undo_until">, now: Date): boolean {
  if (req.status !== "approved" && req.status !== "declined") return false;
  return req.undo_until !== null && new Date(req.undo_until) > now;
}

/** Lower-cased item words for sentences ("wants a bit of your chicken thighs"). */
export function itemWords(req: Pick<ShareRequest, "item">): string {
  return (req.item.short_name || req.item.name).toLowerCase();
}

export function outgoingText(req: ShareRequest): string {
  const owners = joinNames(req.owners ?? []) || strings.requests.theOwners;
  return strings.requests.outgoing(owners, req.amount_words, itemWords(req));
}
