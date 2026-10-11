"use client";

import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "./api/client";
import { kitchenKeys } from "./api/kitchen-hooks";
import type {
  AskAmount,
  InventoryItem,
  InventoryView,
  KitchenPlace,
  KitchenView,
  MoveRequest,
  MoveResponse,
  PartBody,
  Place,
  ShareRequest,
} from "./api/kitchen-types";

/**
 * Kitchen writes, one per user action, using the PRD §21.3 write kinds: the sharing switch and my
 * own item order are set-state PUTs (the only optimistic writes, CLAUDE.md rule 5); moves are a
 * state transition with an Idempotency-Key (never optimistic); edits are guarded PATCHes with
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

/** My order, applied to a cached inventory view (the ids I sent first, the rest after). */
export function withOrder(view: InventoryView, area: Place, ids: readonly string[]): InventoryView {
  const rank = new Map(ids.map((id, n) => [id, n]));
  return {
    ...view,
    areas: view.areas.map((a) => {
      if (a.area !== area) return a;
      const listed = a.items.filter((i) => rank.has(i.id));
      listed.sort((x, y) => (rank.get(x.id) ?? 0) - (rank.get(y.id) ?? 0));
      return { ...a, items: [...listed, ...a.items.filter((i) => !rank.has(i.id))] };
    }),
  };
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
      part: PartBody,
      idempotencyKey: string,
    ): Promise<void> {
      try {
        await api.post(
          `/requests/${req.id}/answer`,
          decision === "split" || decision === "chip_in" ? { decision, ...part } : { decision },
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

    /** Up to 20 moves in one request; a result per item (gestures SPEC §5.2). */
    async move(moves: MoveRequest[], idempotencyKey?: string): Promise<MoveResponse> {
      try {
        return await api.post<MoveResponse>(
          `/houses/${houseId}/items/move`,
          { moves },
          { idempotencyKey },
        );
      } finally {
        await refresh();
      }
    },

    /** PUT set-state of my own order for one section; the cached list reorders at once. */
    async setOrder(place: KitchenPlace, area: Place, ids: string[]): Promise<void> {
      const key = kitchenKeys.inventory(houseId, place);
      await client.cancelQueries({ queryKey: key });
      const before = client.getQueryData<InventoryView>(key);
      if (before) client.setQueryData(key, withOrder(before, area, ids));
      try {
        await api.put(`/houses/${houseId}/me/item-order/${area}`, { item_ids: ids });
      } catch (error) {
        if (before) client.setQueryData(key, before);
        throw error;
      }
    },

    /** The fan's Ask bubble: a bit, no note; returns the request so Undo can cancel it. */
    async askQuick(item: InventoryItem): Promise<{ id: string }> {
      try {
        return await api.post<{ id: string }>(`/inventory/${item.id}/requests`, {
          amount: "a_bit",
          note: null,
        });
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
