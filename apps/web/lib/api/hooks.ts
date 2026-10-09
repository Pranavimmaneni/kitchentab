"use client";

import { useQuery, type QueryClient } from "@tanstack/react-query";
import { api, isApiError } from "./client";
import type { AuthMethods, BillSummary, BillView, HouseView, Me } from "./types";

/** One cache keyed by aggregate (PRD §21.6): `me`, `house:{id}`, `bill:{id}`. */
export const keys = {
  me: ["me"] as const,
  methods: ["auth-methods"] as const,
  house: (id: string) => ["house", id] as const,
  houseBills: (id: string, state?: string) => ["house", id, "bills", state ?? "all"] as const,
  bill: (id: string) => ["bill", id] as const,
};

/** The signed-in user and their houses, or `null` when signed out (401). */
export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async (): Promise<Me | null> => {
      try {
        return await api.get<Me>("/me");
      } catch (error) {
        if (isApiError(error) && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
  });
}

/** Which sign-in methods are configured (C3: phone and Google hidden without keys). */
export function useAuthMethods() {
  return useQuery({
    queryKey: keys.methods,
    queryFn: () => api.get<AuthMethods>("/auth/methods"),
    staleTime: 5 * 60_000,
  });
}

export function useHouse(id: string | null | undefined) {
  return useQuery({
    queryKey: keys.house(id ?? ""),
    queryFn: () => api.get<HouseView>(`/houses/${id}`),
    enabled: Boolean(id),
  });
}

export function useHouseBills(id: string | null | undefined, state?: string) {
  return useQuery({
    queryKey: keys.houseBills(id ?? "", state),
    queryFn: () =>
      api.get<BillSummary[]>(`/houses/${id}/bills${state ? `?state=${encodeURIComponent(state)}` : ""}`),
    enabled: Boolean(id),
  });
}

export function useBill(id: string) {
  return useQuery({
    queryKey: keys.bill(id),
    queryFn: () => api.get<BillView>(`/bills/${id}`),
  });
}

/** Refetch a bill now (after a transition or a conflict). */
export function refreshBill(client: QueryClient, id: string): Promise<void> {
  return client.invalidateQueries({ queryKey: keys.bill(id) });
}
