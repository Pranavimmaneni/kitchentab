/**
 * Response shapes for Milestone 7 routes (apps/api/app/routes/tabs.py, houses.py, account.py).
 * The routes return plain dicts, so these mirror the server views. Money is integer cents and is
 * always the server's number (CLAUDE.md rule 5).
 */
import type { BillState } from "./types";

/** `changed` on an open tab: my share now minus my share when I last looked (FR-35). */
export type TabChange = {
  delta_cents: number;
  since: string;
  /** Who made the latest edit since I looked ("Meera tweaked it Sunday"). */
  by_name: string | null;
  at: string | null;
};

/** One card on Tabs (FR-52): store, date, payer, total, payment progress, my status. */
export type TabCard = {
  id: string;
  store_name: string | null;
  purchase_date: string;
  state: BillState;
  payer_member_id: string;
  payer_name: string | null;
  total_cents: number;
  item_count: number;
  version: number;
  settled_at: string | null;
  /** Open tabs only. */
  paid_count?: number;
  debtor_count?: number;
  my_owed_cents?: number;
  changed?: TabChange | null;
  last_activity?: string | null;
};

export type TabsPage = {
  drafts: TabCard[];
  open: TabCard[];
  history: TabCard[];
  next_cursor: string | null;
};

export type SearchHit = {
  id: string;
  store_name: string | null;
  purchase_date: string;
  state: BillState;
  total_cents: number;
};

/** A bill behind a pair balance; positive cents = they owe me on it. */
export type PairBill = {
  bill_id: string;
  store_name: string | null;
  purchase_date: string;
  cents: number;
};

/** Positive `net_cents` = the other member owes me (FR-51, netted per pair). */
export type BalancePair = {
  member_id: string;
  display_name: string;
  initial: string;
  color: string;
  net_cents: number;
  carried_cents: number;
  bills: PairBill[];
};

export type Balances = {
  net_cents: number;
  owed_to_me_cents: number;
  i_owe_cents: number;
  pairs: BalancePair[];
};

export type Identity = { id: string; provider: "email" | "phone" | "google"; label: string };

export type PaymentHandles = {
  venmo: string | null;
  cashapp: string | null;
  paypal: string | null;
  zelle: string | null;
};
