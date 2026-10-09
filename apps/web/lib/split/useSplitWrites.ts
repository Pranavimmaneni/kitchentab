"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { keys } from "../api/hooks";
import type { AllocationsOut, BillView } from "../api/types";
import { cellKey, createTickCoalescer, type TickChange } from "./coalesce";

export type SplitWriteError = { error: unknown; changes: TickChange[] };

/** Applies set-state results to the cached bill (ticks don't bump versions, PRD §21.3). */
export function applyChanges(view: BillView, changes: readonly TickChange[]): BillView {
  const byItem = new Map<string, TickChange[]>();
  for (const c of changes) byItem.set(c.itemId, [...(byItem.get(c.itemId) ?? []), c]);
  const items = view.items.map((item) => {
    const list = byItem.get(item.id);
    if (!list) return item;
    let allocations = item.allocations;
    for (const c of list) {
      const has = allocations.some((a) => a.participant === c.participant);
      if (c.on && !has) allocations = [...allocations, { participant: c.participant, weight: "1.000" }];
      if (!c.on && has) allocations = allocations.filter((a) => a.participant !== c.participant);
    }
    return { ...item, allocations };
  });
  const assigned = items.filter((i) => i.allocations.length > 0).length;
  return { ...view, items, progress: { assigned, total: items.length } };
}

/**
 * Optimistic split ticks (PRD §21.6): only set-state writes are optimistic; taps on one cell are
 * coalesced for 250 ms; a failed write rolls its cells back and reports the error so the screen
 * can toast "Couldn't save · Retry". Server shares replace local previews when nothing is pending.
 */
export function useSplitWrites(billId: string, onError: (failure: SplitWriteError) => void) {
  const queryClient = useQueryClient();
  const [overrides, setOverrides] = useState<ReadonlyMap<string, boolean>>(new Map());
  const inflight = useRef(new Map<string, { on: boolean; req: number }>());
  const lastReq = useRef(0);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const coalescerRef = useRef<ReturnType<typeof createTickCoalescer> | null>(null);
  // Writes go out one at a time, in tap order: two set-state PUTs for the same cell in flight
  // at once could commit out of order (tick, then undo, ending "on").
  const chain = useRef<Promise<void>>(Promise.resolve());

  const dropOverrides = useCallback((cells: string[]) => {
    if (cells.length === 0) return;
    setOverrides((old) => {
      const next = new Map(old);
      for (const c of cells) next.delete(c);
      return next;
    });
  }, []);

  const serverOn = useCallback(
    (itemId: string, participant: string): boolean => {
      const view = queryClient.getQueryData<BillView>(keys.bill(billId));
      const item = view?.items.find((i) => i.id === itemId);
      return Boolean(item?.allocations.some((a) => a.participant === participant));
    },
    [queryClient, billId],
  );

  const send = useCallback(
    (changes: TickChange[]) => {
      const req = ++lastReq.current;
      for (const c of changes) inflight.current.set(cellKey(c.itemId, c.participant), { on: c.on, req });
      const finished = (): string[] => {
        const done: string[] = [];
        for (const c of changes) {
          const key = cellKey(c.itemId, c.participant);
          if (inflight.current.get(key)?.req === req) {
            inflight.current.delete(key);
            if (!coalescerRef.current?.isPending(c.itemId, c.participant)) done.push(key);
          }
        }
        return done;
      };
      const run = () =>
        api.put<AllocationsOut>(`/bills/${billId}/allocations`, {
          changes: changes.map((c) => ({ item_id: c.itemId, participant: c.participant, on: c.on })),
        });
      const request = chain.current.then(run);
      chain.current = request.then(
        () => undefined,
        () => undefined,
      );
      request
        .then((out) => {
          queryClient.setQueryData<BillView>(keys.bill(billId), (old) => {
            if (!old) return old;
            const next = applyChanges(old, changes);
            return out.shares && req === lastReq.current ? { ...next, shares: out.shares } : next;
          });
          dropOverrides(finished());
        })
        .catch((error: unknown) => {
          dropOverrides(finished());
          void queryClient.invalidateQueries({ queryKey: keys.bill(billId) });
          onErrorRef.current({ error, changes });
        });
    },
    [billId, queryClient, dropOverrides],
  );

  const coalescer = useMemo(
    () =>
      createTickCoalescer({
        send: (changes) => send(changes),
        onSettled: (changes) =>
          dropOverrides(
            changes
              .map((c) => cellKey(c.itemId, c.participant))
              .filter((key) => !inflight.current.has(key)),
          ),
      }),
    [send, dropOverrides],
  );
  coalescerRef.current = coalescer;

  useEffect(
    () => () => {
      coalescer.flush();
    },
    [coalescer],
  );

  /** Sets cells to the desired state (optimistic); `immediate` skips the 250 ms window. */
  const setCells = useCallback(
    (changes: TickChange[], { immediate = false } = {}) => {
      if (changes.length === 0) return;
      setOverrides((old) => {
        const next = new Map(old);
        for (const c of changes) next.set(cellKey(c.itemId, c.participant), c.on);
        return next;
      });
      for (const c of changes) {
        const key = cellKey(c.itemId, c.participant);
        const confirmed = inflight.current.get(key)?.on ?? serverOn(c.itemId, c.participant);
        coalescer.set(c, confirmed);
      }
      if (immediate) coalescer.flush();
    },
    [coalescer, serverOn],
  );

  /** Sends anything waiting, then resolves once every write so far has finished. */
  const settle = useCallback(async () => {
    coalescer.flush();
    await chain.current;
  }, [coalescer]);

  return { overrides, setCells, flush: () => coalescer.flush(), settle, pending: overrides.size > 0 };
}
