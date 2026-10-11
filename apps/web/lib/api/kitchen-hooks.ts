"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AnswerPreview,
  InventoryView,
  KitchenPlace,
  KitchenView,
  PartBody,
  RequestsView,
} from "./kitchen-types";

/** Kitchen reads, keyed under `["house", id]` like the other house screens (PRD §21.6). */
export const kitchenKeys = {
  kitchen: (houseId: string) => ["house", houseId, "kitchen"] as const,
  inventory: (houseId: string, place: KitchenPlace) =>
    ["house", houseId, "inventory", place] as const,
  requests: (houseId: string, box: "in" | "out") => ["house", houseId, "requests", box] as const,
};

/** K1: counts per place, the USE SOON items and how many asks wait on me (FR-63, FR-64). */
export function useKitchen(houseId: string | null | undefined) {
  return useQuery({
    queryKey: kitchenKeys.kitchen(houseId ?? ""),
    queryFn: () => api.get<KitchenView>(`/houses/${houseId}/kitchen`),
    enabled: Boolean(houseId),
  });
}

/**
 * K2/K3: every in-stock item of the place with the server's filter counts. Fetched once with
 * `filter=all`; the screen dims non-matching tiles from the item flags, so switching chips never
 * moves anything or waits on the network (FR-66).
 */
export function useInventory(houseId: string | null | undefined, place: KitchenPlace) {
  return useQuery({
    queryKey: kitchenKeys.inventory(houseId ?? "", place),
    queryFn: () => api.get<InventoryView>(`/houses/${houseId}/inventory?place=${place}&filter=all`),
    enabled: Boolean(houseId),
  });
}

/** K4 (FR-75): FOR YOU / YOU ASKED with counts. */
export function useRequests(houseId: string | null | undefined, box: "in" | "out") {
  return useQuery({
    queryKey: kitchenKeys.requests(houseId ?? "", box),
    queryFn: () => api.get<RequestsView>(`/houses/${houseId}/requests?box=${box}`),
    enabled: Boolean(houseId),
  });
}

/**
 * FR-81: what saying yes with this part would cost (asker's part, share deltas), from the server's
 * `compute_shares` without saving. `body` null = nothing valid to preview yet.
 */
export function useAnswerPreview(
  houseId: string,
  requestId: string,
  decision: "split" | "chip_in",
  body: PartBody | null,
  enabled: boolean,
) {
  const params = new URLSearchParams({ decision });
  if (body?.percent !== undefined) params.set("percent", String(body.percent));
  const cents = body?.amount_cents ?? body?.chip_in_cents;
  if (cents !== undefined) params.set("amount_cents", String(cents));
  return useQuery({
    queryKey: ["house", houseId, "answer-preview", requestId, params.toString()] as const,
    queryFn: () =>
      api.get<AnswerPreview>(`/requests/${requestId}/answer-preview?${params.toString()}`),
    enabled: enabled && body !== null,
    staleTime: 5_000,
  });
}

export type KitchenHistory = {
  used_up: { id: string; name: string; used_up_at: string | null }[];
  answered: { id: string; status: string; resolution: string | null; answered_at: string | null }[];
  me: string;
};

/** FR-80: used-up items and answered asks from the last 30 days (read-only, under House). */
export function useKitchenHistory(houseId: string | null | undefined) {
  return useQuery({
    queryKey: ["house", houseId ?? "", "kitchen-history"] as const,
    queryFn: () => api.get<KitchenHistory>(`/houses/${houseId}/kitchen/history`),
    enabled: Boolean(houseId),
  });
}
