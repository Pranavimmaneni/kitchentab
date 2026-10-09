"use client";

import type { ReactNode } from "react";
import { AppHeader } from "@/components/AppHeader";
import { Skeleton } from "@/components/Skeleton";
import { isApiError } from "@/lib/api/client";
import { useBill } from "@/lib/api/hooks";
import type { BillView } from "@/lib/api/types";
import { ErrorCard } from "./ErrorCard";
import { NotFoundCard } from "./NotFoundCard";

/** Loads `bill:{id}` and shows skeleton, not-found or the generic error before the screen. */
export function BillGate({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: (view: BillView) => ReactNode;
}) {
  const bill = useBill(id);
  if (bill.data) return <>{children(bill.data)}</>;
  const gone = isApiError(bill.error) && (bill.error.status === 404 || bill.error.status === 403);
  return (
    <div className="kt-screen">
      <AppHeader title={title} back={{ href: "/" }} />
      <main className="kt-screen__main">
        {bill.isPending ? <Skeleton variant="card" lines={6} /> : null}
        {gone ? <NotFoundCard /> : null}
        {bill.isError && !gone ? <ErrorCard error={bill.error} onRetry={() => void bill.refetch()} /> : null}
      </main>
    </div>
  );
}
