"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "./client";

/**
 * Open-bill life reads (apps/api/app/routes/lifecycle.py). Keys sit under `["bill", id]` so the
 * sync reducer's bill invalidations cover them; money is never computed on the client.
 */
export const billLifeKeys = {
  money: (id: string) => ["bill", id, "money"] as const,
  activity: (id: string) => ["bill", id, "activity"] as const,
};

export type PaymentStatus = "pending_confirmation" | "confirmed" | "rejected";
export type PaymentMethod = "venmo" | "cashapp" | "paypal" | "zelle" | "cash" | "other";

export type Payment = {
  id: string;
  /** Participant key, `m:{member}` or `g:{guest}`. */
  participant: string;
  direction: "to_payer" | "refund";
  amount_cents: number;
  method: PaymentMethod;
  note: string | null;
  status: PaymentStatus;
  reject_note: string | null;
  version: number;
  created_at: string;
  confirmed_at: string | null;
};

export type PaymentHandles = {
  venmo: string | null;
  cashapp: string | null;
  paypal: string | null;
  zelle: string | null;
};

export type BillMoney = {
  payments: Payment[];
  /** Per non-payer participant: share minus confirmed payments; negative = credit. */
  owed: Record<string, number>;
  /** FR-35: my share now minus when I last looked. */
  changed: { delta_cents: number; since: string } | null;
  version: number;
  payer_handles: PaymentHandles;
};

/** `POST /bills/{id}/payments` (201). `warning` when the same person recorded one < 2 min ago. */
export type RecordedPayment = Payment & {
  warning?: { code: "POSSIBLE_DUPLICATE"; amount_cents: number; recorded_at: string };
};

export type ActivityEntry = {
  id: string;
  type: string;
  actor_name: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
};

export type ActivityPage = { events: ActivityEntry[]; next_cursor: string | null };

export const ACTIVITY_PAGE = 10;

export function useBillMoney(id: string, enabled = true) {
  return useQuery({
    queryKey: billLifeKeys.money(id),
    queryFn: () => api.get<BillMoney>(`/bills/${id}/money`),
    enabled,
  });
}

/** FR-43: newest first, 10 at a time, cursor pagination. */
export function useBillActivity(id: string, enabled = true) {
  return useInfiniteQuery({
    queryKey: billLifeKeys.activity(id),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api.get<ActivityPage>(
        `/bills/${id}/activity?limit=${ACTIVITY_PAGE}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    getNextPageParam: (last) => last.next_cursor,
    enabled,
  });
}
