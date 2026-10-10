"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Toast } from "@/components/Toast";
import { keys, useMe } from "@/lib/api/hooks";
import type { MyHouse } from "@/lib/api/types";
import { strings } from "@/lib/strings";
import { startHouseSync } from "@/lib/sync/house-sync";
import { useOnline } from "@/lib/sync/online";
import { emitRevoked, onRevoked } from "@/lib/sync/revoked";
import { announceSignedOut } from "@/lib/sync/session";
import { useAccountTheme } from "@/lib/theme-account";
import { SavingIndicator } from "./SavingIndicator";

/** Ink top banner while offline (PRD §14A Offline). */
export function OfflineBanner() {
  const online = useOnline();
  if (online) return null;
  return (
    <div className="kt-offline" role="status">
      {strings.sync.offline}
    </div>
  );
}

/** Keeps one house's cache live while signed in (PRD §21.5): stream, relay, catch-up. */
function HouseSync({ house }: { house: MyHouse }) {
  const queryClient = useQueryClient();
  useEffect(() => {
    const sync = startHouseSync({
      houseId: house.id,
      memberId: house.member_id,
      queryClient,
      onRevoked: () => emitRevoked(house.id),
      onSignedOut: announceSignedOut,
    });
    return () => sync.stop();
  }, [house.id, house.member_id, queryClient]);
  return null;
}

/**
 * Signed-in shell: real-time sync for every house the user is in, the offline banner, the
 * account theme, and what happens when the user is removed from a house (SYNC-12): the stream
 * stops, `/me` refreshes and the screen goes home with a notice.
 */
export function SyncRoot({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [revoked, setRevoked] = useState<ReadonlySet<string>>(new Set());
  const housesRef = useRef<MyHouse[]>([]);
  housesRef.current = me.data?.houses ?? [];
  useAccountTheme(me.data?.user.theme);

  useEffect(
    () =>
      onRevoked((houseId) => {
        const house = housesRef.current.find((h) => h.id === houseId);
        setRevoked((old) => new Set(old).add(houseId));
        setNotice(strings.sync.revoked(house?.name ?? strings.sync.thisHouse));
        void queryClient.invalidateQueries({ queryKey: keys.me });
        router.replace("/");
      }),
    [queryClient, router],
  );

  const houses = (me.data?.houses ?? []).filter((h) => !revoked.has(h.id));
  return (
    <>
      <OfflineBanner />
      <SavingIndicator />
      {houses.map((house) => (
        <HouseSync key={`${house.id}:${house.member_id}`} house={house} />
      ))}
      {children}
      <Toast message={notice} onDismiss={() => setNotice(null)} />
    </>
  );
}
