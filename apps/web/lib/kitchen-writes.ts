"use client";

import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "./api/client";
import type {
  AskAmount,
  InventoryItem,
  InventoryView,
  KitchenView,
  Place,
  ShareRequest,
} from "./api/kitchen-types";

/**
 * Kitchen writes, one per user action, using the PRD §21.3 write kinds: the sharing switch is a
 * set-state PUT (the only optimistic write, CLAUDE.md rule 5); edits are guarded PATCHes with
 * If-Match; used up / restore / still good / asks / answers / undo / cancel / leftovers are POSTs
 * with an Idempotency-Key. Nothing is queued offline: a failed write throws to the caller.
 */

/** The sharing switch, applied to a cached item before the server answers (rolled back on error). */
export function withMyConsent(
  item: InventoryItem,
  meMemberId: string,
  open: boolean,
): InventoryItem {
  if (!item.owners.some((o) => o.id === meMemberId)) return item;
  const owners = item.owners.map((o) => (o.id === meMemberId ? { ...o, open_to_share: open } : o));
  const isOpen = owners.every((o) => o.open_to_share);
  return {
    ...item,
    owners,
    is_open: isOpen,
    holdouts: owners.filter((o) => !o.open_to_share).map((o) => o.display_name),
  };
}

function patchCached(
  data: unknown,
  itemId: string,
  patch: (item: InventoryItem) => InventoryItem,
): unknown {
  if (!data || typeof data !== "object") return data;
  if ("areas" in data) {
    const view = data as InventoryView;
    return {
      ...view,
      areas: view.areas.map((a) => ({
        ...a,
        items: a.items.map((i) => (i.id === itemId ? patch(i) : i)),
      })),
    };
  }
  if ("panels" in data) {
    const view = data as KitchenView;
    return {
      ...view,
      panels: {
        fridge: {
          ...view.panels.fridge,
          use_soon: view.panels.fridge.use_soon.map((i) => (i.id === itemId ? patch(i) : i)),
        },
        pantry: {
          ...view.panels.pantry,
          use_soon: view.panels.pantry.use_soon.map((i) => (i.id === itemId ? patch(i) : i)),
        },
      },
    };
  }
  return data;
}

export type LeftoversBody = {
  name: string;
  place: Place;
  icon_key?: string;
  shelf_life_days?: number;
  owner_member_ids?: string[];
  open_to_share?: boolean;
};

export type ItemEditBody = {
  name?: string;
  place?: Place;
  shelf_life_days?: number;
  owner_member_ids?: string[];
};

export function kitchenWrites(client: QueryClient, houseId: string, meMemberId: string) {
  const houseKey: QueryKey = ["house", houseId];
  const refresh = () => client.invalidateQueries({ queryKey: houseKey });

  return {
    refresh,

    /** PUT set-state, optimistic; the cache rolls back if the server refuses (FR-69). */
    async setSharing(item: InventoryItem, open: boolean): Promise<void> {
      await client.cancelQueries({ queryKey: houseKey });
      const before = client.getQueriesData({ queryKey: houseKey });
      client.setQueriesData({ queryKey: houseKey }, (data: unknown) =>
        patchCached(data, item.id, (i) => withMyConsent(i, meMemberId, open)),
      );
      try {
        await api.put(`/inventory/${item.id}/sharing`, { open });
      } catch (error) {
        for (const [key, data] of before) client.setQueryData(key, data);
        throw error;
      } finally {
        void refresh();
      }
    },

    async usedUp(item: InventoryItem): Promise<void> {
      await api.post(`/inventory/${item.id}/used-up`);
      await refresh();
    },

    async restore(item: InventoryItem): Promise<void> {
      await api.post(`/inventory/${item.id}/restore`);
      await refresh();
    },

    async stillGood(item: InventoryItem): Promise<void> {
      await api.post(`/inventory/${item.id}/still-good`);
      await refresh();
    },

    async edit(item: InventoryItem, body: ItemEditBody): Promise<void> {
      try {
        await api.patch(`/inventory/${item.id}`, body, item.version);
      } finally {
        await refresh();
      }
    },

    async ask(
      item: InventoryItem,
      amount: AskAmount,
      note: string,
      idempotencyKey: string,
    ): Promise<void> {
      await api.post(
        `/inventory/${item.id}/requests`,
        { amount, note: note.trim() ? note.trim() : null },
        { idempotencyKey },
      );
      await refresh();
    },

    async cancel(requestId: string): Promise<void> {
      try {
        await api.post(`/requests/${requestId}/cancel`);
      } finally {
        await refresh();
      }
    },

    async answer(
      req: ShareRequest,
      decision: "decline" | "free" | "split" | "chip_in",
      chipInCents: number | null,
      idempotencyKey: string,
    ): Promise<void> {
      try {
        await api.post(
          `/requests/${req.id}/answer`,
          decision === "chip_in" ? { decision, chip_in_cents: chipInCents } : { decision },
          { idempotencyKey },
        );
      } finally {
        await refresh();
      }
    },

    async undo(req: ShareRequest): Promise<void> {
      try {
        await api.post(`/requests/${req.id}/undo`);
      } finally {
        await refresh();
      }
    },

    async addLeftovers(body: LeftoversBody, idempotencyKey: string): Promise<{ id: string }> {
      const out = await api.post<{ id: string }>(`/houses/${houseId}/inventory`, body, {
        idempotencyKey,
      });
      await refresh();
      return out;
    },
  };
}

export type KitchenWrites = ReturnType<typeof kitchenWrites>;

export function useKitchenWrites(houseId: string, meMemberId: string): KitchenWrites {
  const client = useQueryClient();
  return useMemo(() => kitchenWrites(client, houseId, meMemberId), [client, houseId, meMemberId]);
}
