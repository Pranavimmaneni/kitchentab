"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { AppHeader } from "@/components/AppHeader";
import { BottomNav } from "@/components/BottomNav";
import { EmptyState } from "@/components/EmptyState";
import { PillButton } from "@/components/PillButton";
import { Skeleton } from "@/components/Skeleton";
import { ScanIcon } from "@/components/icons";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { useHouseBills, useMe } from "@/lib/api/hooks";
import type { MyHouse } from "@/lib/api/types";
import { shortDate } from "@/lib/bills";
import { formatMoney } from "@/lib/format";
import { strings } from "@/lib/strings";

function HouseTabs({ house }: { house: MyHouse }) {
  const bills = useHouseBills(house.id);
  const drafts = (bills.data ?? []).filter((b) => b.state === "draft");
  const open = (bills.data ?? []).filter((b) => b.state === "open");
  return (
    <section className="kt-field" aria-labelledby={`house-${house.id}`}>
      <h2 id={`house-${house.id}`} className="kt-house-heading">
        {strings.app.brandLine(house.name)}
      </h2>
      <PillButton href={`/bills/new?house=${house.id}`} fullWidth icon={<ScanIcon size={20} />}>
        {strings.landing.snap}
      </PillButton>
      {bills.isPending ? <Skeleton lines={3} /> : null}
      {bills.isError ? <ErrorCard error={bills.error} onRetry={() => void bills.refetch()} /> : null}
      {drafts.length > 0 ? (
        <ul className="kt-list">
          {drafts.map((b) => (
            <li key={b.id}>
              <Link href={`/bills/${b.id}/review`} className="kt-tab-card" data-draft="">
                <span className="kt-tab-card__store">
                  {strings.bill.draft(b.store_name ?? strings.bill.untitled)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      {open.length > 0 ? (
        <>
          <h3 className="kt-house-heading">
            {strings.landing.openTabs} · {open.length}
          </h3>
          <ul className="kt-list">
            {open.map((b) => (
              <li key={b.id}>
                <Link href={`/bills/${b.id}`} className="kt-tab-card">
                  <span className="kt-field">
                    <span className="kt-tab-card__store">{b.store_name ?? strings.bill.untitled}</span>
                    <span className="kt-tab-card__meta">{shortDate(b.purchase_date)}</span>
                  </span>
                  <span className="kt-tab-card__total">{formatMoney(b.total_cents)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

export function LandingScreen() {
  const router = useRouter();
  const me = useMe();
  const houses = me.data?.houses ?? [];
  const noHouse = me.isSuccess && !me.isFetching && me.data !== null && houses.length === 0;

  // Signed in with no house yet (e.g. back from Google): straight to C.
  useEffect(() => {
    if (noHouse) router.replace("/houses/new");
  }, [noHouse, router]);

  return (
    <div className="kt-screen">
      <AppHeader
        large
        stepLabel={houses[0] ? strings.app.brandLine(houses[0].name) : strings.app.name.toUpperCase()}
        title={strings.kitchen.title}
      />
      <main className="kt-screen__main">
        {me.isPending ? <Skeleton lines={4} /> : null}
        {me.isError ? <ErrorCard error={me.error} onRetry={() => void me.refetch()} /> : null}
        {noHouse ? (
          <EmptyState
            title={strings.landing.noHouse}
            action={<PillButton href="/houses/new">{strings.landing.startHouse}</PillButton>}
          />
        ) : null}
        {houses.map((house) => (
          <HouseTabs key={house.id} house={house} />
        ))}
        <p className="kt-footnote">{strings.kitchen.comingSoon}</p>
      </main>
      <BottomNav />
    </div>
  );
}
