"use client";

import Link from "next/link";
import { BottomNav } from "@/components/BottomNav";
import { FoodIcon, foodPastel } from "@/components/FoodIcon";
import { FridgeArt, PantryArt } from "@/components/KitchenArt";
import { OwnerDots } from "@/components/OwnerDots";
import { Skeleton } from "@/components/Skeleton";
import { ScanIcon } from "@/components/icons";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { HouseGate, type HouseContext } from "@/components/screens/HouseGate";
import { HouseHeader, ScanLink } from "@/components/screens/HouseHeader";
import { useKitchen } from "@/lib/api/kitchen-hooks";
import type { KitchenPanel, KitchenPlace } from "@/lib/api/kitchen-types";
import { useBalances } from "@/lib/api/tabs-hooks";
import { formatMoney } from "@/lib/format";
import { joinNames, ownerNames, soonText } from "@/lib/kitchen";
import { strings } from "@/lib/strings";
import { signedMoney } from "@/lib/tabs";

const SOON_ROWS = 3;

/** K1 · The kitchen, the house landing page at `/` (PRD §19.8, FR-63, FR-64). */
export function KitchenScreen() {
  return <HouseGate title={strings.kitchen.title}>{(ctx) => <Kitchen {...ctx} />}</HouseGate>;
}

function Kitchen({ house, houses, select }: HouseContext) {
  const kitchen = useKitchen(house.id);
  const balances = useBalances(house.id);
  const net = balances.data?.net_cents;

  return (
    <div className="kt-screen kt-kitchen" data-wide="">
      <HouseHeader
        house={house}
        houses={houses}
        onSelect={select}
        title={strings.kitchen.title}
        actions={
          <ScanLink houseId={house.id}>
            <ScanIcon size={20} />
          </ScanLink>
        }
      />
      <main className="kt-kitchen__main">
        <div className="kt-kitchen__pills">
          <Link
            href="/tabs"
            className="kt-pill-link"
            data-tone={net !== undefined && net < 0 ? "warn" : undefined}
          >
            {net === undefined ? (
              <span className="kt-pill-link__muted">{strings.tabs.balance}</span>
            ) : net > 0 ? (
              <>
                <span className="kt-hl">{signedMoney(net)}</span> {strings.kitchen.owedSuffix}
              </>
            ) : net < 0 ? (
              strings.kitchen.youOwe(formatMoney(-net))
            ) : (
              strings.kitchen.allSquare
            )}
          </Link>
          <Link href="/kitchen/requests" className="kt-pill-link" data-testid="asks-pill">
            <span className="kt-hl" data-tone="violet">
              {kitchen.data?.asks_for_me ?? 0}
            </span>{" "}
            {strings.kitchen.asksSuffix(kitchen.data?.asks_for_me ?? 0)}
          </Link>
        </div>
        {kitchen.isError ? (
          <ErrorCard error={kitchen.error} onRetry={() => void kitchen.refetch()} />
        ) : null}
        <div className="kt-kitchen__panels">
          {(["fridge", "pantry"] as const).map((place) => (
            <Panel
              key={place}
              place={place}
              panel={kitchen.data?.panels[place]}
              meMemberId={house.member_id}
            />
          ))}
        </div>
      </main>
      <BottomNav />
    </div>
  );
}

function Panel({
  place,
  panel,
  meMemberId,
}: {
  place: KitchenPlace;
  panel: KitchenPanel | undefined;
  meMemberId: string;
}) {
  const name = strings.kitchen.panelName[place];
  const soon = panel?.use_soon ?? [];
  const shown = soon.slice(0, SOON_ROWS);
  const more = soon.length - shown.length;
  return (
    <Link
      href={`/kitchen/${place}`}
      className="kt-panel"
      data-place={place}
      aria-label={panel ? strings.kitchen.panelLabel(place, panel.count, soon.length) : name}
    >
      <span className="kt-panel__art">{place === "fridge" ? <FridgeArt /> : <PantryArt />}</span>
      <span className="kt-panel__info">
        <span className="kt-panel__titles">
          <span className="kt-panel__name">{name}</span>
          <span className="kt-panel__count">{strings.kitchen.panelItems(panel?.count ?? 0)}</span>
        </span>
        {!panel ? (
          <Skeleton lines={2} />
        ) : panel.count === 0 ? (
          <span className="kt-panel__empty">{strings.kitchen.emptyTitle}</span>
        ) : soon.length === 0 ? (
          <span className="kt-panel__empty kt-mono-label">{strings.kitchen.nothingSoon}</span>
        ) : (
          <>
            <span className="kt-soon-stamp" data-tilt={place}>
              {strings.kitchen.useSoon(soon.length)}
            </span>
            <span className="kt-soon-rows">
              {shown.map((item) => (
                <span key={item.id} className="kt-soon-row">
                  <span
                    className="kt-food kt-food--ring"
                    style={{ background: foodPastel(item.icon_key, item.place) }}
                  >
                    <FoodIcon iconKey={item.icon_key} place={item.place} size={20} />
                  </span>
                  <span className="kt-soon-row__text">
                    <span className="kt-soon-row__name">{item.short_name || item.name}</span>
                    <span className="kt-soon-row__meta">
                      <span className="kt-soon-row__left">{soonText(item)}</span>
                      <OwnerDots owners={item.owners} size={14} />
                      <span className="sr-only">{joinNames(ownerNames(item, meMemberId))}</span>
                    </span>
                  </span>
                </span>
              ))}
            </span>
            {more > 0 ? <span className="kt-panel__more">{strings.kitchen.more(more)}</span> : null}
          </>
        )}
      </span>
    </Link>
  );
}
