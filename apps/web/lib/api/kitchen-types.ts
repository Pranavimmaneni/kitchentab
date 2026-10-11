/**
 * Response shapes for the fridge and pantry routes (apps/api/app/routes/kitchen.py and
 * services/inventory.py views). Owners, sharing state, use-soon flags and share deltas are the
 * server's; the UI only displays them (CLAUDE.md rule 5).
 */
import type { BillState } from "./types";

export type Place = "fridge" | "freezer" | "pantry";
export type KitchenPlace = "fridge" | "pantry";
export type KitchenFilter = "all" | "mine" | "open" | "use_soon";
export type AskAmount = "a_bit" | "half" | "all";

export type ItemOwner = {
  id: string;
  display_name: string;
  initial: string;
  color: string;
  open_to_share: boolean;
};

export type InventoryItem = {
  id: string;
  name: string;
  short_name: string;
  icon_key: string | null;
  place: Place;
  category: string | null;
  price_cents: number | null;
  stocked_on: string;
  shelf_life_days: number;
  status: "in_stock" | "used_up" | "removed";
  version: number;
  source: {
    bill_id: string;
    store_name: string | null;
    purchase_date: string;
    state: BillState;
  } | null;
  owners: ItemOwner[];
  holdouts: string[];
  is_open: boolean;
  open_for_me: boolean;
  granted_to_me: boolean;
  granted_by?: string | null;
  mine: boolean;
  use_soon: boolean;
  probably_gone: boolean;
  days_left: number;
  soon_label: string | null;
  my_request: { id: string; amount: AskAmount } | null;
  /** Present on `/inventory` responses: whether the item matches the requested filter. */
  matches?: boolean;
};

export type FilterCounts = Record<KitchenFilter, number>;

export type InventoryArea = { area: Place; items: InventoryItem[] };

export type InventoryView = {
  place: KitchenPlace;
  filter: KitchenFilter;
  counts: FilterCounts;
  areas: InventoryArea[];
};

export type KitchenPanel = { count: number; use_soon: InventoryItem[] };

export type KitchenView = {
  house: { id: string; name: string };
  panels: Record<KitchenPlace, KitchenPanel>;
  asks_for_me: number;
};

export type MemberChip = { id: string; display_name?: string; initial?: string; color?: string };

export type RequestStatus = "pending" | "approved" | "declined" | "expired" | "cancelled";
export type Resolution = "free" | "split" | "chip_in";
export type AnswerOption = "free" | "split" | "chip_in";

export type ShareRequest = {
  id: string;
  item: {
    id: string;
    name: string;
    short_name: string;
    icon_key: string | null;
    bill_id: string | null;
  };
  requester: MemberChip;
  amount: AskAmount;
  amount_words: string;
  note: string | null;
  status: RequestStatus;
  resolution: Resolution | null;
  chip_in_cents: number | null;
  answered_by: MemberChip | null;
  answered_at: string | null;
  undo_until: string | null;
  expires_at: string;
  created_at: string;
  version: number;
  /** Active owners' names, the source tab and the chip-in ceiling (K4 copy). */
  owners?: string[];
  source?: { store_name: string | null; state: BillState } | null;
  line_total_cents?: number | null;
  /** FOR YOU, pending only. */
  options?: AnswerOption[];
  split_deltas?: Record<string, number>;
  chip_in_suggestion_cents?: number | null;
  co_owners?: string[];
};

/** FR-81: how much of the line the asker pays, sent with a split or chip-in answer. */
export type PartBody = { percent?: number; amount_cents?: number; chip_in_cents?: number };

/** GET /requests/{id}/answer-preview: the asker's part and every share delta, nothing saved. */
export type AnswerPreview = {
  decision: "split" | "chip_in";
  line_total_cents: number | null;
  asker_cents: number | null;
  deltas: Record<string, number>;
};

export type RequestsView = {
  box: "in" | "out";
  requests: ShareRequest[];
  counts: { in: number; out: number };
};
