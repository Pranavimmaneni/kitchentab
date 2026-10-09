"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { BottomSheet } from "@/components/BottomSheet";
import { PillButton } from "@/components/PillButton";
import { PencilIcon, ScanIcon, UploadIcon } from "@/components/icons";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { api, newId } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import { actionKey, clearActionKey } from "@/lib/auth-flow";
import { strings } from "@/lib/strings";

/** Photo and PDF capture arrive with extraction in Milestone 5. */
export const SCAN_ENABLED = process.env.NEXT_PUBLIC_SCAN_ENABLED === "true";

/** Capture sheet (PRD §14A 1): only manual entry until scanning ships. */
export function CaptureScreen({ houseParam }: { houseParam: string | null }) {
  const router = useRouter();
  const me = useMe();
  const [open, setOpen] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const houseId =
    houseParam && me.data?.houses.some((h) => h.id === houseParam)
      ? houseParam
      : (me.data?.houses[0]?.id ?? null);

  async function createDraft(): Promise<void> {
    if (!houseId || creating) return;
    setCreating(true);
    setError(null);
    const keyName = `new-bill:${houseId}`;
    try {
      const bill = await api.post<{ id: string }>(`/houses/${houseId}/bills`, {}, {
        idempotencyKey: actionKey(keyName, newId),
      });
      clearActionKey(keyName);
      router.replace(`/bills/${bill.id}/review`);
    } catch (err) {
      setCreating(false);
      setError(err);
    }
  }

  return (
    <div className="kt-screen">
      <AppHeader title={strings.capture.title} back={{ href: "/" }} />
      <main className="kt-screen__main">
        {error ? <ErrorCard error={error} onRetry={() => void createDraft()} /> : null}
        {!open ? (
          <PillButton fullWidth onClick={() => setOpen(true)} icon={<ScanIcon size={20} />}>
            {strings.landing.snap}
          </PillButton>
        ) : null}
      </main>
      <BottomSheet open={open} onClose={() => setOpen(false)} title={strings.capture.title}>
        <div className="kt-form">
          {SCAN_ENABLED ? (
            <>
              <PillButton variant="secondary" size={52} fullWidth icon={<ScanIcon size={20} />} onClick={() => void createDraft()}>
                {strings.capture.photo}
              </PillButton>
              <PillButton variant="secondary" size={52} fullWidth icon={<UploadIcon size={20} />} onClick={() => void createDraft()}>
                {strings.capture.upload}
              </PillButton>
            </>
          ) : null}
          <PillButton
            variant="secondary"
            size={52}
            fullWidth
            icon={<PencilIcon size={20} />}
            loading={creating}
            disabled={!houseId}
            onClick={() => void createDraft()}
          >
            {strings.capture.manual}
          </PillButton>
        </div>
      </BottomSheet>
    </div>
  );
}
