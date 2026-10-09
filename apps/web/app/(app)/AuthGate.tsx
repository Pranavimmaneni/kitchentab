"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useMe } from "@/lib/api/hooks";

/** Sends people whose session expired (401 on `/me`) back to `/start`. */
export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const me = useMe();
  useEffect(() => {
    if (me.isSuccess && me.data === null) router.replace("/start");
  }, [me.isSuccess, me.data, router]);
  return <>{children}</>;
}
