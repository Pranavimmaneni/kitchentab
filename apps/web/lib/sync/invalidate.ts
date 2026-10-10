/**
 * Turns reducer targets into TanStack Query invalidations, debounced 150 ms and coalesced per
 * target (PRD §21.6), so a burst of 20 ticks costs one refetch per open query, not twenty.
 * `refetch: "none"` only marks queries stale (own echoes: the screen already shows the result).
 */
import type { Query, QueryClient } from "@tanstack/react-query";
import type { BillView } from "../api/types";
import { targetKey, type Target } from "./plan";

export const INVALIDATE_DEBOUNCE_MS = 150;

export type Refetch = "active" | "none";

export type Invalidator = {
  schedule: (targets: readonly Target[], refetch?: Refetch) => void;
  flush: () => void;
  /** Refetch every cached query now (resync, PRD §21.5). */
  everything: () => void;
  cancel: () => void;
};

/** Cached bill views of one house (bills carry their house id). */
export function cachedBills(client: QueryClient, houseId: string): BillView[] {
  return client
    .getQueriesData<BillView>({ queryKey: ["bill"] })
    .filter(([key, data]) => key.length === 2 && data?.bill?.house_id === houseId)
    .map(([, data]) => data!);
}

export function createInvalidator(
  client: QueryClient,
  houseId: string,
  delayMs = INVALIDATE_DEBOUNCE_MS,
): Invalidator {
  const queued = new Map<string, { target: Target; refetch: Refetch }>();
  let timer: ReturnType<typeof setTimeout> | null = null;

  const billHouse = (id: unknown): string | undefined =>
    typeof id === "string"
      ? client.getQueryData<BillView>(["bill", id])?.bill?.house_id
      : undefined;
  const inHouse = (query: Query): boolean => {
    const house = billHouse(query.queryKey[1]);
    return house === undefined || house === houseId;
  };

  function run(target: Target, refetchType: Refetch): void {
    switch (target.kind) {
      case "bill":
        void client.invalidateQueries({ queryKey: ["bill", target.id], refetchType });
        return;
      case "billLife":
        void client.invalidateQueries({ queryKey: ["bill", target.id, "money"], refetchType });
        void client.invalidateQueries({ queryKey: ["bill", target.id, "activity"], refetchType });
        return;
      case "bills":
        void client.invalidateQueries({
          predicate: (q) => q.queryKey[0] === "bill" && inHouse(q),
          refetchType,
        });
        return;
      case "payments":
        void client.invalidateQueries({
          predicate: (q) =>
            q.queryKey[0] === "bill" &&
            (q.queryKey[2] === "money" || q.queryKey[2] === "activity") &&
            inHouse(q),
          refetchType,
        });
        return;
      case "house":
        void client.invalidateQueries({
          predicate: (q) => q.queryKey[0] !== "bill" && q.queryKey.includes(houseId),
          refetchType,
        });
        return;
      case "me":
        void client.invalidateQueries({ queryKey: ["me"], refetchType });
        return;
    }
  }

  function flush(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    const list = [...queued.values()];
    queued.clear();
    for (const { target, refetch } of list) run(target, refetch);
  }

  return {
    schedule(targets, refetch = "active") {
      for (const target of targets) {
        const key = targetKey(target);
        const before = queued.get(key);
        queued.set(key, { target, refetch: before?.refetch === "active" ? "active" : refetch });
      }
      if (queued.size > 0 && timer === null) timer = setTimeout(flush, delayMs);
    },
    flush,
    everything() {
      queued.clear();
      if (timer !== null) clearTimeout(timer);
      timer = null;
      void client.invalidateQueries();
    },
    cancel() {
      queued.clear();
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
