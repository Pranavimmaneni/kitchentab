"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { SyncRoot } from "@/components/sync/SyncRoot";
import { keys, useMe } from "@/lib/api/hooks";
import { forgetHouse } from "@/lib/house";
import { onSignedOut } from "@/lib/sync/session";

/**
 * Sends people whose session ended back to `/start` and keeps the houses live. The session ends
 * when `/me` answers 401, when any other request does (`kt:unauthorized`, re-checked through
 * `/me`), or when this or another tab signs out, or another device signs out everywhere
 * (`onSignedOut`, AUTH-09).
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useMe();
  useEffect(() => {
    if (me.isSuccess && me.data === null) router.replace("/start");
  }, [me.isSuccess, me.data, router]);

  useEffect(() => {
    const leave = onSignedOut(() => {
      forgetHouse();
      queryClient.clear();
      router.replace("/start");
    });
    const recheck = (event: Event) => {
      const path = (event as CustomEvent<{ path?: string }>).detail?.path;
      if (path !== "/me") void queryClient.invalidateQueries({ queryKey: keys.me });
    };
    window.addEventListener("kt:unauthorized", recheck);
    return () => {
      leave();
      window.removeEventListener("kt:unauthorized", recheck);
    };
  }, [queryClient, router]);

  return <SyncRoot>{children}</SyncRoot>;
}
