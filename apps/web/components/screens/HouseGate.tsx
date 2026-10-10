"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { AppHeader } from "@/components/AppHeader";
import { BottomNav } from "@/components/BottomNav";
import { Skeleton } from "@/components/Skeleton";
import type { Me, MyHouse } from "@/lib/api/types";
import { useCurrentHouse } from "@/lib/house";
import { strings } from "@/lib/strings";
import { ErrorCard } from "./ErrorCard";

export type HouseContext = {
  house: MyHouse;
  houses: MyHouse[];
  me: Me;
  select: (id: string) => void;
};

/**
 * Loads the signed-in user and the current house (FR-08) before a house screen renders. Signed in
 * with no house yet → C (`/houses/new`); a lapsed session is handled by AuthGate.
 */
export function HouseGate({
  title,
  nav = true,
  children,
}: {
  title: string;
  nav?: boolean;
  children: (ctx: HouseContext) => ReactNode;
}) {
  const router = useRouter();
  const { me, houses, house, select } = useCurrentHouse();
  const noHouse = me.isSuccess && !me.isFetching && me.data !== null && houses.length === 0;

  useEffect(() => {
    if (noHouse) router.replace("/houses/new");
  }, [noHouse, router]);

  if (house && me.data) return <>{children({ house, houses, me: me.data, select })}</>;
  return (
    <div className="kt-screen">
      <AppHeader large title={title} stepLabel={strings.app.name.toUpperCase()} />
      <main className="kt-screen__main">
        {me.isError ? (
          <ErrorCard error={me.error} onRetry={() => void me.refetch()} />
        ) : (
          <Skeleton lines={4} />
        )}
      </main>
      {nav ? <BottomNav /> : null}
    </div>
  );
}
