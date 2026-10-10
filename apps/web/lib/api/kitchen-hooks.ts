"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { InventoryView, KitchenPlace, KitchenView, RequestsView } from "./kitchen-types";

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
