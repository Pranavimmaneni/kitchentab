"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { AreaGrid, type TileGestures } from "@/components/ItemTile";
import { ItemSheet, type ItemSheetActions } from "@/components/ItemSheet";
import { LeftoversSheet } from "@/components/LeftoversSheet";
import { PillButton } from "@/components/PillButton";
import { Skeleton } from "@/components/Skeleton";
import { Toast } from "@/components/Toast";
import { CheckIcon, LockIcon } from "@/components/icons";
import { ActionBar } from "@/components/screens/ActionBar";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { HouseGate, type HouseContext } from "@/components/screens/HouseGate";
import {
  bubblesFor,
  destinations,
  inverseMoves,
  type BubbleAction,
} from "@/features/kitchen/gestures/actions";
import { STACK_MAX, UNDO_MS } from "@/features/kitchen/gestures/constants";
import { DropBar, GestureOverlay, SelectBar } from "@/features/kitchen/gestures/GestureLayer";
import type { Effect } from "@/features/kitchen/gestures/reducer";
import { usePlaceGestures } from "@/features/kitchen/gestures/usePlaceGestures";
import { isApiError } from "@/lib/api/client";
import { useHouse } from "@/lib/api/hooks";
import { useInventory, useKitchen } from "@/lib/api/kitchen-hooks";
import type {
  InventoryArea,
  InventoryItem,
  KitchenFilter,
  KitchenPlace,
  Place,
} from "@/lib/api/kitchen-types";
import { haptic } from "@/lib/haptics";
import { FILTERS, FILTER_STORAGE_KEY, isFilter, joinNames } from "@/lib/kitchen";
import { useKitchenWrites } from "@/lib/kitchen-writes";
import { strings } from "@/lib/strings";
import { gestureStrings } from "@/features/kitchen/gestures/strings";

const SEEN_KEY = "kt.kitchen.seen";

/** K2 · The fridge and K3 · The pantry: one component, two places (PRD §19.8, FR-65..FR-69). */
export function PlaceScreen({ place }: { place: KitchenPlace }) {
  return (
    <HouseGate title={strings.place.titles[place]} nav={false}>
      {(ctx) => <Place place={place} {...ctx} />}
    </HouseGate>
  );
}

type ToastState = {
  message: string;
  /** What screen readers hear instead, e.g. "Yogurt moved to pantry. Undo available." */
  spoken?: string;
  variant?: "info" | "error";
  action?: { label: string; run: () => void };
  duration?: number;
  id: number;
};

function readFilter(): KitchenFilter {
  try {
    const saved = window.localStorage.getItem(FILTER_STORAGE_KEY);
    return isFilter(saved) ? saved : "all";
  } catch {
    return "all";
  }
}

/** Ids this member has not seen before on this device: their tiles pop in once (PRD §19.8 motion). */
function useNewIds(ids: readonly string[] | undefined): ReadonlySet<string> {
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const joined = ids?.join(",");
  useEffect(() => {
    if (!ids) return;
    let seen: string[] = [];
    try {
      seen = JSON.parse(window.localStorage.getItem(SEEN_KEY) ?? "[]") as string[];
    } catch {
      seen = [];
    }
    const known = new Set(seen);
    setFresh(new Set(ids.filter((id) => !known.has(id))));
    try {
      window.localStorage.setItem(
        SEEN_KEY,
        JSON.stringify([...new Set([...seen, ...ids])].slice(-500)),
      );
    } catch {
      // ignore
    }
    // Only when the set of ids changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [joined]);
  return fresh;
}

function Place({ place, house }: HouseContext & { place: KitchenPlace }) {
  const inventory = useInventory(house.id, place);
  const kitchen = useKitchen(house.id);
  const houseView = useHouse(house.id);
  const writes = useKitchenWrites(house.id, house.member_id);
  const [filter, setFilterState] = useState<KitchenFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  // Select mode (FR-G05): pick items, then move them together from the bottom bar.
  const [selectMode, setSelectMode] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [moving, setMoving] = useState<Place | null>(null);
  // A reorder just dropped: drawn at once, before the cache catches up, with no slide.
  const [localOrder, setLocalOrder] = useState<{ area: Place; ids: string[] } | null>(null);

  useEffect(() => setFilterState(readFilter()), []);
  const setFilter = (next: KitchenFilter) => {
    setFilterState(next);
    try {
      window.localStorage.setItem(FILTER_STORAGE_KEY, next);
    } catch {
      // remembered for this visit only
    }
  };

  const show = useCallback(
    (next: Omit<ToastState, "id">) => setToast({ ...next, id: Date.now() }),
    [],
  );
  const fail = useCallback(
    (error: unknown, retry?: () => void) =>
      show({
        message: isApiError(error) ? error.title : strings.item.couldntSave,
        variant: "error",
        action: retry ? { label: strings.item.retry, run: retry } : undefined,
      }),
    [show],
  );

  const view = inventory.data;
  const areas = useMemo(() => {
    const base = orderAreas(view?.areas ?? [], place);
    return localOrder
      ? base.map((a) =>
          a.area === localOrder.area ? { ...a, items: inOrder(a.items, localOrder.ids) } : a,
        )
      : base;
  }, [view, place, localOrder]);
  const allItems = useMemo(() => areas.flatMap((a) => a.items), [areas]);
  const selected = allItems.find((i) => i.id === selectedId) ?? null;
  const popIds = useNewIds(view ? allItems.map((i) => i.id) : undefined);
  const members = (houseView.data?.members ?? []).filter((m) => m.status === "active");
  const asks = kitchen.data?.asks_for_me ?? 0;
  const byId = useMemo(() => new Map(allItems.map((i) => [i.id, i])), [allItems]);
  const locs = useMemo(
    () => Object.fromEntries(allItems.map((i) => [i.id, i.place])) as Record<string, Place>,
    [allItems],
  );
  const orders = useMemo(
    () =>
      Object.fromEntries(areas.map((a) => [a.area, a.items.map((i) => i.id)])) as Partial<
        Record<Place, string[]>
      >,
    [areas],
  );
  // Picked items that are gone (used up elsewhere) drop out of the pick.
  const livePicked = useMemo(() => picked.filter((id) => byId.has(id)), [picked, byId]);
  const pickedRef = useRef(livePicked);
  pickedRef.current = livePicked;

  const leaveSelect = useCallback(() => {
    setSelectMode(false);
    setPicked([]);
  }, []);

  /** One move request for up to 20 items and a toast with Undo (FR-G07, FR-G08). */
  const runMove = useCallback(
    async (ids: readonly string[], to: Place): Promise<boolean> => {
      if (ids.length === 0) return false;
      const from = new Map(ids.map((id) => [id, byId.get(id)?.place]));
      const name = (id: string) => {
        const it = byId.get(id);
        return it ? it.short_name || it.name : "";
      };
      const toName = gestureStrings.places[to];
      let res;
      try {
        res = await writes.move(ids.map((id) => ({ item_id: id, to })));
      } catch (error) {
        fail(error);
        return false;
      }
      const moved = res.results
        .filter((r) => r.result === "moved")
        .map((r) => ({ item_id: r.item_id, from: (r.from ?? from.get(r.item_id)) as Place, to }));
      const there = res.results.filter((r) => r.result === "skipped_already_there").length;
      const gone = res.results.length - moved.length - there;
      if (moved.length) haptic("success");
      const message =
        moved.length === 0
          ? gone
            ? gestureStrings.gone(gone)
            : gestureStrings.alreadyThere(gestureStrings.placeLower[to])
          : there
            ? gestureStrings.movedMixed(moved.length, there)
            : moved.length === 1
              ? gestureStrings.movedOne(name(moved[0]!.item_id), toName)
              : gestureStrings.movedMany(moved.length, toName);
      const lower = gestureStrings.placeLower[to];
      show({
        message,
        spoken: moved.length
          ? gestureStrings.announceMoved(
              moved.length === 1
                ? name(moved[0]!.item_id)
                : gestureStrings.itemsCount(moved.length),
              lower,
            )
          : undefined,
        duration: moved.length ? UNDO_MS : undefined,
        action: moved.length
          ? {
              label: strings.toast.undo,
              run: () => {
                writes
                  .move(inverseMoves(moved))
                  .then((back) => {
                    const changed = back.results.filter(
                      (r) => r.result === "skipped_changed",
                    ).length;
                    if (changed) show({ message: gestureStrings.changedSince(changed) });
                  })
                  .catch((error) => fail(error));
              },
            }
          : undefined,
      });
      return true;
    },
    [byId, writes, fail, show],
  );

  const actions: ItemSheetActions = {
    setSharing: (item, open) => {
      writes
        .setSharing(item, open)
        .catch((error) => fail(error, () => actions.setSharing(item, open)));
    },
    usedUp: async (item) => {
      try {
        await writes.usedUp(item);
      } catch (error) {
        fail(error);
        return;
      }
      setSelectedId(null);
      show({
        message: strings.item.usedUpToast(item.short_name || item.name),
        duration: UNDO_MS,
        action: {
          label: strings.toast.undo,
          run: () => void writes.restore(item).catch((error) => fail(error)),
        },
      });
    },
    stillGood: async (item) => {
      try {
        await writes.stillGood(item);
        show({ message: strings.item.stillGoodToast });
      } catch (error) {
        fail(error);
      }
    },
    edit: async (item, body) => {
      try {
        await writes.edit(item, body);
        show({ message: strings.item.saved });
      } catch (error) {
        fail(error);
        throw error;
      }
    },
    ask: async (item, amount, note, key) => {
      try {
        await writes.ask(item, amount, note, key);
        show({
          message: strings.item.sentToast(joinNames(item.owners.map((o) => o.display_name))),
        });
      } catch (error) {
        fail(error);
        throw error;
      }
    },
    cancel: async (_item, requestId) => {
      try {
        await writes.cancel(requestId);
        show({ message: strings.item.cancelledToast });
      } catch (error) {
        fail(error);
      }
    },
    // No-gesture paths (SPEC §8): Move to, and Move earlier / later in my own order.
    move: async (item, to) => {
      if (await runMove([item.id], to)) setSelectedId(null);
    },
    reorder: async (item, delta) => {
      const ids = orders[item.place] ?? [];
      const at = ids.indexOf(item.id);
      const next = at + delta;
      if (at < 0 || next < 0 || next >= ids.length) return;
      const order = [...ids];
      order.splice(at, 1);
      order.splice(next, 0, item.id);
      try {
        await writes.setOrder(place, item.place, order);
      } catch (error) {
        fail(error);
      }
    },
    orderEnds: (item) => {
      const ids = orders[item.place] ?? [];
      return { first: ids[0] === item.id, last: ids[ids.length - 1] === item.id };
    },
  };
  const actionsRef = useRef(actions);
  actionsRef.current = actions;

  /** The fan's three bubbles (SPEC §3, FR-G04). */
  const runBubble = useCallback(
    (item: InventoryItem, action: BubbleAction) => {
      switch (action.kind) {
        case "move":
          void runMove([item.id], action.to);
          return;
        case "help":
          show({ message: gestureStrings.helpYourself });
          return;
        case "usedUp":
          writes
            .usedUp(item)
            .then(() => {
              haptic("success");
              show({
                message: strings.item.usedUpToast(item.short_name || item.name),
                duration: UNDO_MS,
                action: {
                  label: strings.toast.undo,
                  run: () => void writes.restore(item).catch((error) => fail(error)),
                },
              });
            })
            .catch((error) => fail(error));
          return;
        case "cancelAsk":
          writes
            .cancel(action.requestId)
            .then(() => show({ message: strings.item.cancelledToast }))
            .catch((error) => fail(error));
          return;
        case "ask":
          writes
            .askQuick(item)
            .then((req) => {
              haptic("success");
              show({
                message: gestureStrings.askedToast(action.owner, item.short_name || item.name),
                duration: UNDO_MS,
                action: {
                  label: strings.toast.undo,
                  run: () => void writes.cancel(req.id).catch((error) => fail(error)),
                },
              });
            })
            .catch((error) => fail(error));
      }
    },
    [runMove, show, writes, fail],
  );

  const onEffect = useCallback(
    (effect: Effect) => {
      switch (effect.type) {
        case "choose": {
          const item = byId.get(effect.itemId);
          if (item) runBubble(item, bubblesFor(item)[effect.index]);
          return;
        }
        case "reorder":
          setLocalOrder({ area: effect.section, ids: effect.order });
          writes
            .setOrder(place, effect.section, effect.order)
            .catch((error) => fail(error))
            .finally(() => setLocalOrder(null));
          return;
        case "move": {
          const fromPicks = effect.itemIds.every((id) => pickedRef.current.includes(id));
          void runMove(effect.itemIds, effect.to).then((ok) => {
            if (!ok) return;
            if (fromPicks) leaveSelect();
            else setPicked((now) => now.filter((id) => !effect.itemIds.includes(id)));
          });
          return;
        }
        case "blocked":
          show({ message: gestureStrings.alreadyThere(gestureStrings.placeLower[effect.to]) });
          return;
        default:
          // tap: the tile's own click handles it; home: CSS glides the tiles back.
          return;
      }
    },
    [byId, runBubble, writes, place, fail, runMove, leaveSelect, show],
  );

  const gestures = usePlaceGestures(rootRef, {
    selectMode,
    picked: livePicked,
    items: locs,
    orders,
    onEffect,
  });
  const g = gestures.state;
  const dragging = g.phase === "DRAGGING" || g.phase === "COLLECTING";

  const toggle = (item: InventoryItem) => {
    if (!livePicked.includes(item.id) && livePicked.length >= STACK_MAX) {
      show({ message: gestureStrings.pickLimit(STACK_MAX) });
      return;
    }
    setPicked((now) =>
      now.includes(item.id) ? now.filter((id) => id !== item.id) : [...now, item.id],
    );
    haptic("tick");
  };
  const openItem = (item: InventoryItem) => {
    if (gestures.consumeClick()) return; // the click that ends a hold or a drag
    if (selectMode) toggle(item);
    else setSelectedId(item.id);
  };

  // Escape leaves select mode when nothing is lifted (SPEC §4).
  useEffect(() => {
    if (!selectMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && g.phase === "IDLE") leaveSelect();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectMode, g.phase, leaveSelect]);

  const pickedSet = useMemo(() => new Set(livePicked), [livePicked]);
  const ghostIds = useMemo(
    () => (g.phase === "COLLECTING" ? new Set(g.stack) : new Set<string>()),
    [g.phase, g.stack],
  );
  const gestureOn = g.phase !== "IDLE";
  const shownAreas =
    gestureOn && g.section
      ? areas.map((a) => (a.area === g.section ? { ...a, items: inOrder(a.items, g.original) } : a))
      : areas;
  const pinned = useMemo(
    () => (gestureOn ? new Set([...g.stack, ...(g.itemId ? [g.itemId] : [])]) : new Set<string>()),
    [gestureOn, g.stack, g.itemId],
  );
  // #3: what the overlay draws survives an item leaving the inventory mid-drag (SYNC-G2 note).
  const seen = useRef(new Map<string, InventoryItem>());
  if (!gestureOn) seen.current = new Map(byId);
  else for (const [id, it] of byId) seen.current.set(id, it);
  const tileGestures: TileGestures = {
    handlers: gestures.bind,
    shiftOf: gestures.shiftOf,
    selectMode,
    picked: pickedSet,
    gapId: g.phase === "DRAGGING" ? g.itemId : null,
    ghostIds,
    pinned,
    pressedId: g.phase === "PRESSING" ? g.itemId : null,
  };
  const pickedPlaces = livePicked.map((id) => byId.get(id)!.place);
  const lifted = g.itemId ? byId.get(g.itemId) : undefined;

  const counts = view?.counts;
  return (
    <div
      ref={rootRef}
      className="kt-screen kt-place"
      data-place={place}
      data-gesture={g.phase === "IDLE" ? undefined : g.phase.toLowerCase()}
      data-select={selectMode || undefined}
      data-settling={localOrder ? "" : undefined}
    >
      <AppHeader
        back={{
          href: "/",
          label: selectMode ? gestureStrings.doneLabel : strings.place.back,
          onClick: selectMode ? leaveSelect : undefined,
        }}
        stepLabel={strings.place.step(house.name, counts?.all ?? 0)}
        title={strings.place.titles[place]}
        actions={
          <button
            type="button"
            className="kt-select-toggle"
            data-on={selectMode || undefined}
            aria-pressed={selectMode}
            aria-label={selectMode ? gestureStrings.doneLabel : gestureStrings.selectLabel}
            onClick={() => (selectMode ? leaveSelect() : setSelectMode(true))}
          >
            {selectMode ? gestureStrings.done : gestureStrings.select}
          </button>
        }
      />
      <div className="kt-filters" role="toolbar" aria-label={strings.place.filterLabel}>
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            className="kt-filter"
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
          >
            {strings.place.filterChip(strings.place.filters[f], counts?.[f] ?? 0)}
          </button>
        ))}
      </div>
      <main className="kt-screen__main kt-place__main">
        {inventory.isError ? (
          <ErrorCard error={inventory.error} onRetry={() => void inventory.refetch()} />
        ) : !view ? (
          <Skeleton variant="card" lines={5} />
        ) : (
          <section
            className="kt-appliance"
            data-place={place}
            aria-label={strings.place.titles[place]}
          >
            {shownAreas.map((area) => {
              const dropHere = g.phase === "DRAGGING" && g.overSection === area.area;
              return (
                <div
                  key={area.area}
                  className="kt-area"
                  data-look={lookOf(area.area)}
                  data-kt-area={area.area}
                  data-drop={dropHere || undefined}
                >
                  {place === "fridge" ? (
                    <span className="kt-area__label" aria-hidden="true">
                      {dropHere
                        ? gestureStrings.dropToMove(gestureStrings.placeLower[area.area])
                        : strings.place.areas[area.area]}
                    </span>
                  ) : null}
                  {area.items.length === 0 ? (
                    <p className="kt-area__empty">{strings.place.emptyArea}</p>
                  ) : null}
                  <AreaGrid
                    items={area.items}
                    filter={filter}
                    meMemberId={house.member_id}
                    label={strings.place.areaLabel(
                      strings.place.areas[area.area],
                      area.items.length,
                    )}
                    popIds={popIds}
                    onOpen={openItem}
                    gestures={tileGestures}
                  />
                </div>
              );
            })}
          </section>
        )}
        {selectMode ? (
          <p className="kt-select-hint">{gestureStrings.selectHint}</p>
        ) : (
          <ul className="kt-legend" aria-hidden="true">
            <li>
              <span className="kt-legend__dot" data-kind="open">
                <CheckIcon size={10} strokeWidth={3.5} />
              </span>
              {strings.place.legendOpen}
            </li>
            <li>
              <span className="kt-legend__dot" data-kind="ask">
                <LockIcon size={10} strokeWidth={3} />
              </span>
              {strings.place.legendAsk}
            </li>
            <li>
              <span className="kt-legend__dot" data-kind="soon" />
              {strings.place.legendSoon}
            </li>
          </ul>
        )}
      </main>
      <ActionBar>
        {dragging ? (
          <DropBar state={g} />
        ) : selectMode ? (
          <SelectBar
            count={livePicked.length}
            destinations={destinations(pickedPlaces)}
            primary={place === "fridge" ? "pantry" : "fridge"}
            busy={moving}
            onMove={(to) => {
              setMoving(to);
              void runMove(livePicked, to).then((ok) => {
                setMoving(null);
                if (ok) leaveSelect();
              });
            }}
          />
        ) : (
          <div className="kt-place__bar">
            <PillButton variant="dashed" size={52} onClick={() => setAdding(true)}>
              {place === "fridge" ? strings.place.addLeftovers : strings.place.addSomething}
            </PillButton>
            <Link
              href="/kitchen/requests"
              className="kt-pill kt-asks-link"
              data-variant="primary"
              data-size="52"
            >
              <span>{strings.place.asks(asks)}</span>
            </Link>
          </div>
        )}
      </ActionBar>
      <GestureOverlay
        state={g}
        items={seen.current}
        bubbles={lifted && !selectMode ? bubblesFor(lifted) : null}
        liftedAt={gestures.liftedRect()}
        onChoose={gestures.choose}
        onCancel={gestures.cancel}
      />
      <ItemSheet
        item={selected}
        meMemberId={house.member_id}
        members={members}
        onClose={() => setSelectedId(null)}
        actions={actions}
      />
      <LeftoversSheet
        open={adding}
        onClose={() => setAdding(false)}
        place={place}
        meMemberId={house.member_id}
        members={members}
        onAdd={async (body, key) => {
          try {
            await writes.addLeftovers(body, key);
            setAdding(false);
            show({ message: strings.leftovers.added(body.name) });
          } catch (error) {
            fail(error);
            throw error;
          }
        }}
      />
      <Toast
        message={toast?.message ?? null}
        spoken={toast?.spoken}
        variant={toast?.variant}
        duration={toast?.duration}
        onUndo={toast?.action?.run}
        actionLabel={toast?.action?.label}
        onDismiss={() => setToast(null)}
      />
    </div>
  );
}

/** Items in the order of `ids` (those not in it keep their order, at the end). */
function inOrder(items: readonly InventoryItem[], ids: readonly string[]): InventoryItem[] {
  const rank = new Map(ids.map((id, n) => [id, n]));
  const listed = items.filter((i) => rank.has(i.id));
  listed.sort((a, b) => rank.get(a.id)! - rank.get(b.id)!);
  return [...listed, ...items.filter((i) => !rank.has(i.id))];
}

/** Freezer above Refrigerated (Fridge.dc.html); the pantry is one area with no label. */
function orderAreas(areas: readonly InventoryArea[], place: KitchenPlace): InventoryArea[] {
  const order: Place[] = place === "fridge" ? ["freezer", "fridge"] : ["pantry"];
  return order.map((area) => areas.find((a) => a.area === area) ?? { area, items: [] });
}

function lookOf(area: Place): "frost" | "glass" | "wood" {
  return area === "freezer" ? "frost" : area === "fridge" ? "glass" : "wood";
}
