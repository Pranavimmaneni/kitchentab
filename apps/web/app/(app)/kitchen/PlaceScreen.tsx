"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { AreaGrid } from "@/components/ItemTile";
import { ItemSheet, type ItemSheetActions } from "@/components/ItemSheet";
import { LeftoversSheet } from "@/components/LeftoversSheet";
import { PillButton } from "@/components/PillButton";
import { Skeleton } from "@/components/Skeleton";
import { Toast } from "@/components/Toast";
import { CheckIcon, LockIcon } from "@/components/icons";
import { ActionBar } from "@/components/screens/ActionBar";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { HouseGate, type HouseContext } from "@/components/screens/HouseGate";
import { isApiError } from "@/lib/api/client";
import { useHouse } from "@/lib/api/hooks";
import { useInventory, useKitchen } from "@/lib/api/kitchen-hooks";
import type { InventoryArea, KitchenFilter, KitchenPlace, Place } from "@/lib/api/kitchen-types";
import { FILTERS, FILTER_STORAGE_KEY, isFilter, joinNames } from "@/lib/kitchen";
import { useKitchenWrites } from "@/lib/kitchen-writes";
import { strings } from "@/lib/strings";

const SEEN_KEY = "kt.kitchen.seen";
const USED_UP_UNDO_MS = 5000;

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
  const areas = useMemo(() => orderAreas(view?.areas ?? [], place), [view, place]);
  const allItems = useMemo(() => areas.flatMap((a) => a.items), [areas]);
  const selected = allItems.find((i) => i.id === selectedId) ?? null;
  const popIds = useNewIds(view ? allItems.map((i) => i.id) : undefined);
  const members = (houseView.data?.members ?? []).filter((m) => m.status === "active");
  const asks = kitchen.data?.asks_for_me ?? 0;

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
        duration: USED_UP_UNDO_MS,
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
  };

  const counts = view?.counts;
  return (
    <div className="kt-screen kt-place" data-place={place}>
      <AppHeader
        back={{ href: "/", label: strings.place.back }}
        stepLabel={strings.place.step(house.name, counts?.all ?? 0)}
        title={strings.place.titles[place]}
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
            {areas.map((area) => (
              <div key={area.area} className="kt-area" data-look={lookOf(area.area)}>
                {place === "fridge" ? (
                  <span className="kt-area__label" aria-hidden="true">
                    {strings.place.areas[area.area]}
                  </span>
                ) : null}
                {area.items.length === 0 ? (
                  <p className="kt-area__empty">{strings.place.emptyArea}</p>
                ) : null}
                <AreaGrid
                  items={area.items}
                  filter={filter}
                  meMemberId={house.member_id}
                  label={strings.place.areaLabel(strings.place.areas[area.area], area.items.length)}
                  popIds={popIds}
                  onOpen={(item) => setSelectedId(item.id)}
                />
              </div>
            ))}
          </section>
        )}
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
      </main>
      <ActionBar>
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
      </ActionBar>
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
        variant={toast?.variant}
        duration={toast?.duration}
        onUndo={toast?.action?.run}
        actionLabel={toast?.action?.label}
        onDismiss={() => setToast(null)}
      />
    </div>
  );
}

/** Freezer above Refrigerated (Fridge.dc.html); the pantry is one area with no label. */
function orderAreas(areas: readonly InventoryArea[], place: KitchenPlace): InventoryArea[] {
  const order: Place[] = place === "fridge" ? ["freezer", "fridge"] : ["pantry"];
  return order.map((area) => areas.find((a) => a.area === area) ?? { area, items: [] });
}

function lookOf(area: Place): "frost" | "glass" | "wood" {
  return area === "freezer" ? "frost" : area === "fridge" ? "glass" : "wood";
}
