/**
 * Response shapes of the API routes the web app reads (apps/api/app/routes/*.py). Most routes
 * return plain dicts, so the generated OpenAPI schema only types request bodies; these mirror the
 * service views (`member_view`, `bill_view`, `join_view`). Money is integer cents.
 */
import type { components } from "./schema";

export type Schemas = components["schemas"];

export type AuthMethods = Schemas["Methods"];
export type SendCodeBody = Schemas["SendCode"];
export type SendCodeOut = Schemas["SendCodeOut"];
export type VerifyCodeBody = Schemas["VerifyCode"];
export type SignedIn = Schemas["SignedIn"];
export type Channel = SendCodeBody["channel"];

export type User = {
  id: string;
  display_name: string;
  email: string | null;
  phone: string | null;
  theme: "system" | "light" | "dark";
  timezone: string | null;
  notify_email: boolean;
  share_by_default: boolean;
};

export type MyHouse = { id: string; name: string; member_id: string; role: "admin" | "member" };

export type Me = { user: User; houses: MyHouse[] };

export type MemberStatus = "placeholder" | "active" | "left" | "removed" | "anonymized";

export type Member = {
  id: string;
  display_name: string;
  initial: string;
  color: string;
  sort_order: number;
  role: "admin" | "member";
  status: MemberStatus;
  claimed: boolean;
  version: number;
};

export type HouseView = {
  house: { id: string; name: string; version: number; timezone: string };
  me: { member_id: string; role: "admin" | "member" };
  members: Member[];
  invite_token: string | null;
};

export type CreatedHouse = {
  house: { id: string; name: string; version: number };
  member_id: string;
  members: Member[];
  invite_token: string;
};

export type JoinView = {
  house: { id: string; name: string };
  admin_name: string | null;
  member_count: number;
  placeholders: Member[];
  signed_in: boolean;
  my_member: Member | null;
};

export type ClaimOut = { house_id: string; member: Member };

export type Storage = "fridge" | "freezer" | "pantry" | "none";
export type BillState = "draft" | "open" | "settled" | "voided";

export type BillAllocation = {
  participant: string;
  weight: string;
  /** Fixed fraction of the line from a custom split (ADR-0030); null for weighted ticks. */
  share_num?: number | null;
  share_den?: number | null;
};

export type BillItem = {
  id: string;
  position: number;
  name: string;
  raw_text: string | null;
  quantity: string;
  unit_price_cents: number | null;
  discount_cents: number;
  line_total_cents: number;
  taxable: boolean;
  confidence: string | null;
  flagged: boolean;
  storage: Storage;
  category: string | null;
  icon_key: string | null;
  shelf_life_days: number | null;
  version: number;
  allocations: BillAllocation[];
};

export type FeeKind = "fee" | "tip" | "order_discount" | "adjustment";
export type BillFee = { id: string; kind: FeeKind; label: string; amount_cents: number };

export type Participant = {
  key: string;
  kind: "member" | "guest";
  id: string;
  display_name: string;
  initial: string;
  color: string;
  sort_order: number;
  status: MemberStatus | "guest";
  excluded: boolean;
  claimed: boolean;
};

export type Reconciliation = {
  receipt_total_cents: number | null;
  computed_cents: number;
  difference_cents: number;
  overridden: boolean;
  override_reason: string | null;
  adjustment_cents: number;
  ok: boolean;
};

export type BillHeader = {
  id: string;
  house_id: string;
  store_name: string | null;
  purchase_date: string;
  currency: string;
  state: BillState;
  version: number;
  payer_member_id: string;
  subtotal_cents: number;
  tax_cents: number;
  total_cents: number;
  receipt_total_cents: number | null;
  extraction_status: string | null;
  sent_at: string | null;
  settled_at: string | null;
  void_reason: string | null;
  link_token?: string | null;
};

export type BillView = {
  bill: BillHeader;
  items: BillItem[];
  fees: BillFee[];
  participants: Participant[];
  shares: Record<string, number>;
  shares_final: boolean;
  progress: { assigned: number; total: number };
  reconciliation: Reconciliation;
  duplicate_of: {
    id: string;
    store_name: string | null;
    purchase_date: string;
    state: BillState;
  } | null;
  me: { member_id: string | null; is_payer: boolean; can_edit: boolean };
  /** A scan that finished after items were typed by hand (drafts only; PRD §21.6, SYNC-15). */
  scanned_offer?: ScannedOffer | null;
  /** Open 60+ days: the payer sees a gentle banner (PRD §15, EDGE-15-25). */
  long_open?: boolean;
};

/** `receipts.offer_view`: "Use the scanned items instead?" */
export type ScannedOffer = {
  extraction_id: string;
  item_count: number;
  total_cents: number | null;
  computed_cents: number;
  store_name: string | null;
  source: string;
  created_at: string;
};

/** `GET /b/{token}`: the bill view plus the join picker (no link token). */
export type BillLinkView = BillView & {
  house: { name: string };
  payer_name: string | null;
  join: {
    placeholders: Member[];
    my_member: Member | null;
    signed_in: boolean;
    invite_token: null;
  };
};

export type BillSummary = {
  id: string;
  store_name: string | null;
  purchase_date: string;
  state: BillState;
  total_cents: number;
  payer_member_id: string;
  version: number;
};

export type AllocationChange = { item_id: string; participant: string; on: boolean };
export type AllocationsOut = { applied: AllocationChange[]; shares: Record<string, number> | null };
export type FillRestOut = { filled: string[]; shares: Record<string, number> };
export type SendOut = {
  id: string;
  state: "open";
  version: number;
  link_token: string;
  shares: Record<string, number>;
};
export type GuestOut = { id: string; key: string; display_name: string; link_token: string };
export type HeaderPatch = Schemas["Header"];
export type NewItemBody = Schemas["NewItem"];
export type ItemPatchBody = Schemas["ItemPatch"];
