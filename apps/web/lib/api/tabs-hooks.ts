"use client";

import { useInfiniteQuery, useQuery, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { Balances, Identity, PaymentHandles, SearchHit, TabsPage } from "./tabs-types";

/**
 * Milestone 7 reads. Keys sit under `["house", id]` so one invalidation (or the sync client's
 * house event) refreshes every house-scoped screen (PRD §21.6).
 */
export const tabKeys = {
  tabs: (houseId: string) => ["house", houseId, "tabs"] as const,
  balances: (houseId: string) => ["house", houseId, "balances"] as const,
  search: (houseId: string, q: string) => ["house", houseId, "search", q] as const,
  identities: ["me", "identities"] as const,
  handles: ["me", "payment-handles"] as const,
};

/** Drafts, open tabs and settled history; history pages 20 at a time by cursor (FR-52). */
export function useTabs(houseId: string | null | undefined) {
  return useInfiniteQuery({
    queryKey: tabKeys.tabs(houseId ?? ""),
    queryFn: ({ pageParam }) =>
      api.get<TabsPage>(
        `/houses/${houseId}/tabs${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: Boolean(houseId),
  });
}

export function useBalances(houseId: string | null | undefined) {
  return useQuery({
    queryKey: tabKeys.balances(houseId ?? ""),
    queryFn: () => api.get<Balances>(`/houses/${houseId}/balances`),
    enabled: Boolean(houseId),
  });
}

/** FR-53: store or item name, 2 to 60 characters. */
export function useSearch(houseId: string | null | undefined, q: string) {
  const term = q.trim();
  return useQuery({
    queryKey: tabKeys.search(houseId ?? "", term),
    queryFn: () => api.get<SearchHit[]>(`/houses/${houseId}/search?q=${encodeURIComponent(term)}`),
    enabled: Boolean(houseId) && term.length >= 2 && term.length <= 60,
    staleTime: 10_000,
  });
}

export function useIdentities() {
  return useQuery({
    queryKey: tabKeys.identities,
    queryFn: () => api.get<Identity[]>("/me/identities"),
  });
}

export function usePaymentHandles() {
  return useQuery({
    queryKey: tabKeys.handles,
    queryFn: () => api.get<PaymentHandles>("/me/payment-handles"),
  });
}

/** After a house-scoped write: refetch everything the house screens show. */
export function refreshHouse(client: QueryClient, houseId: string): Promise<void> {
  return client.invalidateQueries({ queryKey: ["house", houseId] });
}
