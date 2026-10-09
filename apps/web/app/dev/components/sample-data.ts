/**
 * Sample data for /dev/components, taken from the mockups and packages/fixtures/money_cases.json
 * (`shoprite_sent`: shares $23.51 / $33.91 / $18.87 / $18.89). Amounts are integer cents and are
 * never computed here; they stand in for what the API will return.
 */
import type { ActivityEvent } from "@/components/ActivityTimeline";
import type { BalanceLine } from "@/components/BalanceTicker";
import type { Storage } from "@/components/ItemRow";
import type { SplitItem, SplitMember, SplitTotal } from "@/components/SplitGrid";

export const HOUSE = "Apt 4B";

export const MEMBERS: SplitMember[] = [
  { id: "pranav", name: "Pranav", tag: "YOU", colorIndex: 0 },
  { id: "arjun", name: "Arjun", tag: "ARJ", colorIndex: 1 },
  { id: "meera", name: "Meera", tag: "MEE", colorIndex: 2 },
  { id: "kabir", name: "Kabir", tag: "KAB", colorIndex: 3 },
];

export const EXTRA_MEMBERS: SplitMember[] = [
  { id: "riya", name: "Riya", tag: "RIY", colorIndex: 4 },
  { id: "sam", name: "Sam", tag: "SAM", colorIndex: 5 },
];

export const ITEMS: SplitItem[] = [
  { id: "milk", name: "Whole milk", priceCents: 429, note: "$1.43 × 3" },
  { id: "eggs", name: "Eggs, 18 ct", priceCents: 549, note: "$1.37 × 4" },
  { id: "rice", name: "Basmati rice", priceCents: 1499, note: "$3.75 × 4" },
  { id: "yogurt", name: "Greek yogurt", priceCents: 679, note: "JUST MEERA" },
  { id: "bananas", name: "Bananas", priceCents: 132, note: "$0.66 × 2" },
  { id: "towels", name: "Paper towels", priceCents: 999, note: "$2.50 × 4" },
  { id: "soap", name: "Dish soap", priceCents: 349, note: "$0.87 × 4" },
  { id: "chicken", name: "Chicken thighs", priceCents: 1137, note: "$5.68 × 2" },
  { id: "oatmilk", name: "Oat milk", priceCents: 459, note: "JUST KABIR" },
  { id: "spinach", name: "Baby spinach", priceCents: 399, note: "$2.00 × 2" },
  { id: "bars", name: "Protein bars", priceCents: 1799, note: "JUST ARJUN" },
  { id: "coffee", name: "Coffee beans", priceCents: 999, note: "$5.00 × 2" },
];

const ALL = ["pranav", "arjun", "meera", "kabir"];

/** `shoprite_sent` allocations. */
export const ASSIGNED: Record<string, string[]> = {
  milk: ["pranav", "arjun", "meera"],
  eggs: ALL,
  rice: ALL,
  yogurt: ["meera"],
  bananas: ["pranav", "kabir"],
  towels: ALL,
  soap: ALL,
  chicken: ["pranav", "arjun"],
  oatmilk: ["kabir"],
  spinach: ["pranav", "meera"],
  bars: ["arjun"],
  coffee: ["pranav", "kabir"],
};

/** The mockup's in-progress state: 9 of 12 assigned. */
export const ASSIGNED_PARTIAL: Record<string, string[]> = {
  ...ASSIGNED,
  spinach: [],
  bars: [],
  coffee: [],
};

export const TOTALS: SplitTotal[] = [
  { memberId: "pranav", amountCents: 2351 },
  { memberId: "arjun", amountCents: 3391 },
  { memberId: "meera", amountCents: 1887 },
  { memberId: "kabir", amountCents: 1889 },
];

export const TOTALS_PARTIAL: SplitTotal[] = [
  { memberId: "pranav", amountCents: 1652 },
  { memberId: "arjun", amountCents: 1582 },
  { memberId: "meera", amountCents: 1695 },
  { memberId: "kabir", amountCents: 1392 },
];

export const SIX_MEMBERS: SplitMember[] = [...MEMBERS, ...EXTRA_MEMBERS];

export const SIX_ASSIGNED: Record<string, string[]> = {
  milk: ["pranav", "arjun", "meera", "riya"],
  eggs: ["pranav", "arjun", "meera", "kabir", "riya", "sam"],
  rice: ["pranav", "arjun", "meera", "kabir", "riya", "sam"],
  yogurt: ["meera", "sam"],
};

export const SIX_TOTALS: SplitTotal[] = [
  { memberId: "pranav", amountCents: 525 },
  { memberId: "arjun", amountCents: 525 },
  { memberId: "meera", amountCents: 865 },
  { memberId: "kabir", amountCents: 418 },
  { memberId: "riya", amountCents: 525 },
  { memberId: "sam", amountCents: 758 },
];

export const REVIEW_ITEMS: { id: string; name: string; priceCents: number; storage: Storage; flagged?: boolean }[] = [
  { id: "milk", name: "Whole milk 1 gal", priceCents: 429, storage: "fridge" },
  { id: "rice", name: "Basmati rice 10 lb", priceCents: 1499, storage: "pantry" },
  { id: "bananas", name: "Bananas 2.1 lb", priceCents: 132, storage: "pantry", flagged: true },
  { id: "dumplings", name: "Pork dumplings", priceCents: 899, storage: "freezer" },
  { id: "towels", name: "Paper towels 6 pk", priceCents: 999, storage: "none" },
];

export const ACTIVITY: ActivityEvent[] = [
  {
    id: "e5",
    kind: "edit",
    text: "Meera hopped on the coffee beans. Your share fell $1.69, Kabir’s fell $1.68.",
    when: "SUN 10/04 · 9:12 PM",
  },
  { id: "e4", kind: "payment", text: "Kabir paid $18.89.", when: "SAT 10/03 · 6:40 PM" },
  { id: "e3", kind: "system", text: "Meera confirmed her items.", when: "SAT 10/03 · 10:05 AM" },
  { id: "e2", kind: "system", text: "You sent the tab to the house.", when: "FRI 10/02 · 7:31 PM" },
  {
    id: "e1",
    kind: "system",
    text: "You scanned a ShopRite receipt, 12 items.",
    when: "FRI 10/02 · 7:24 PM",
  },
];

export const BALANCES: BalanceLine[] = [
  { id: "b1", from: "Arjun", to: "You", amountCents: 3391, kind: "owed" },
  { id: "b2", from: "Meera", to: "You", amountCents: 814, kind: "owed" },
  { id: "b3", from: "You", to: "Kabir", amountCents: 168, kind: "owing" },
];
