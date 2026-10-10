"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { PillButton } from "@/components/PillButton";
import { ReceiptCard } from "@/components/ReceiptCard";
import { Skeleton } from "@/components/Skeleton";
import { XIcon } from "@/components/icons";
import { api, isApiError, newId } from "@/lib/api/client";
import { keys } from "@/lib/api/hooks";
import type { BillView, ScannedOffer } from "@/lib/api/types";
import { formatMoney } from "@/lib/format";
import { ACCEPT_ANY } from "@/lib/receipts/files";
import { checkPick } from "@/lib/receipts/pick";
import { startScan, type ScanJob } from "@/lib/receipts/scan-job";
import type { ReceiptRow } from "@/lib/receipts/upload";
import { strings } from "@/lib/strings";
import { useOnline } from "@/lib/sync/online";
import { useFocusTrap } from "@/lib/use-focus-trap";

const S = strings.scan;

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="kt-screen">
      <AppHeader stepLabel={strings.review.step} title={strings.review.title} back={{ href: "/" }} />
      <main className="kt-screen__main">{children}</main>
    </div>
  );
}

/** "READING YOUR RECEIPT…" with skeleton lines; "Enter by hand instead" (PRD §14A 2). */
export function ScanReading({ job, onEnterByHand }: { job: ScanJob; onEnterByHand: () => void }) {
  const pct = Math.round(job.progress * 100);
  return (
    <Frame>
      <ReceiptCard heading={S.reading} hideHeading>
        <div className="kt-scan" data-phase={job.phase}>
          <Skeleton lines={9} label={S.progress(pct)} />
          <p className="kt-scan__status" aria-live="polite">
            {S.reading}
          </p>
          <button type="button" className="kt-text-link" onClick={onEnterByHand}>
            {S.enterByHandInstead}
          </button>
        </div>
      </ReceiptCard>
    </Frame>
  );
}

/**
 * "Couldn't read that one." + Try again / Enter by hand (EXTRACT-06). With the files still in
 * memory Try again resumes the scan; after a reload it asks for the file again.
 */
export function ScanFailed({
  billId,
  message,
  onRetry,
  onEnterByHand,
}: {
  billId: string;
  message: string | null;
  onRetry: (() => void) | null;
  onEnterByHand: () => void;
}) {
  const online = useOnline();
  const input = useRef<HTMLInputElement>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const detailId = useId();

  async function chosen(files: File[]): Promise<void> {
    setRefusal(null);
    const picked = await checkPick(files);
    if (typeof picked === "string") setRefusal(picked);
    else startScan(billId, picked);
  }

  return (
    <Frame>
      <ReceiptCard heading={S.failedTitle}>
        <div className="kt-scan" data-phase="failed">
          <p className="kt-scan__title" role="alert" aria-describedby={message ? detailId : undefined}>
            {S.failedTitle}
          </p>
          {message ? (
            <p id={detailId} className="kt-scan__detail">
              {message}
            </p>
          ) : null}
          <div className="kt-scan__actions">
            <PillButton
              size={52}
              fullWidth
              disabled={!online}
              disabledReason={online ? undefined : strings.button.needsConnection}
              onClick={() => (onRetry ? onRetry() : input.current?.click())}
            >
              {onRetry ? S.tryAgain : S.chooseAgain}
            </PillButton>
            <PillButton variant="secondary" size={52} fullWidth onClick={onEnterByHand}>
              {S.enterByHand}
            </PillButton>
          </div>
          {refusal ? (
            <p className="kt-capture__error" role="alert">
              {refusal}
            </p>
          ) : null}
          <input
            ref={input}
            type="file"
            accept={ACCEPT_ANY}
            multiple
            hidden
            data-kt-input="again"
            onChange={(e) => {
              const files = e.currentTarget.files ? Array.from(e.currentTarget.files) : [];
              e.currentTarget.value = "";
              if (files.length) void chosen(files);
            }}
          />
        </div>
      </ReceiptCard>
    </Frame>
  );
}

/** "Use the scanned items instead?" (PRD §21.6, SYNC-15): apply or dismiss the stored scan. */
export function OfferBanner({
  view,
  offer,
  onDone,
}: {
  view: BillView;
  offer: ScannedOffer;
  onDone: (message: string) => void;
}) {
  const client = useQueryClient();
  const online = useOnline();
  const [busy, setBusy] = useState<"use" | "keep" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const keyRef = useRef<Record<string, string>>({});
  const total = formatMoney(offer.total_cents ?? offer.computed_cents, view.bill.currency);

  async function decide(kind: "use" | "keep"): Promise<void> {
    setBusy(kind);
    setError(null);
    const action = kind === "use" ? "apply" : "dismiss";
    // One key per decision on this offer, so a retried tap returns the first answer.
    const key = (keyRef.current[`${offer.extraction_id}:${action}`] ??= newId());
    try {
      await api.post(`/bills/${view.bill.id}/extractions/${offer.extraction_id}/${action}`, undefined, {
        idempotencyKey: key,
      });
      await client.invalidateQueries({ queryKey: keys.bill(view.bill.id) });
      onDone(kind === "use" ? S.offer.used : S.offer.kept);
    } catch (err) {
      setError(isApiError(err) ? err.title : strings.error.couldntSave);
      await client.invalidateQueries({ queryKey: keys.bill(view.bill.id) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="kt-banner kt-offer" aria-labelledby="scan-offer-title">
      <p id="scan-offer-title" className="kt-banner__text">
        {S.offer.title}
      </p>
      <p className="kt-offer__detail">{S.offer.detail(offer.item_count, total)}</p>
      <div className="kt-offer__actions">
        <PillButton
          size={44}
          variant="ink"
          loading={busy === "use"}
          disabled={!online || busy !== null}
          disabledReason={online ? undefined : strings.button.needsConnection}
          onClick={() => void decide("use")}
        >
          {S.offer.use}
        </PillButton>
        <PillButton
          size={44}
          variant="secondary"
          loading={busy === "keep"}
          disabled={!online || busy !== null}
          onClick={() => void decide("keep")}
        >
          {S.offer.keep}
        </PillButton>
      </div>
      {error ? (
        <p className="kt-capture__error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

type ViewUrl = { url: string; mime: string; expires_at: string };

/**
 * The stored receipt (FR-18): a thumbnail that opens full screen, where the browser's own pinch
 * zoom works. URLs are short-lived and fetched when needed; the service worker never caches them.
 */
export function ReceiptPhoto({ billId }: { billId: string }) {
  const receipts = useQuery({
    queryKey: ["bill", billId, "receipts"],
    queryFn: () => api.get<(ReceiptRow & { deleted: boolean })[]>(`/bills/${billId}/receipts`),
    staleTime: 60_000,
  });
  const first = receipts.data?.find((r) => !r.deleted);
  const view = useQuery({
    queryKey: ["receipt-view", first?.id ?? ""],
    queryFn: () => api.get<ViewUrl>(`/receipts/${first!.id}/view`),
    enabled: Boolean(first),
    staleTime: 4 * 60_000, // URLs live 5 minutes
  });
  const [open, setOpen] = useState(false);
  if (!first || !view.data) return null;
  if (first.mime === "application/pdf") {
    return (
      <a className="kt-photo-thumb kt-photo-thumb--pdf" href={view.data.url} target="_blank" rel="noreferrer noopener">
        {S.photo.openPdf}
      </a>
    );
  }
  return (
    <>
      <button type="button" className="kt-photo-thumb" onClick={() => setOpen(true)} aria-label={S.photo.open}>
        {/* eslint-disable-next-line @next/next/no-img-element -- short-lived signed URL on storage's origin */}
        <img src={view.data.url} alt="" width={56} height={72} />
      </button>
      {open ? <PhotoViewer url={view.data.url} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function PhotoViewer({ url, onClose }: { url: string; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, true, onClose);
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);
  return (
    <div ref={ref} className="kt-photo-viewer" role="dialog" aria-modal="true" aria-label={S.photo.alt}>
      <button type="button" className="kt-icon-button kt-photo-viewer__close" aria-label={S.photo.close} onClick={onClose}>
        <XIcon size={20} />
      </button>
      {/* eslint-disable-next-line @next/next/no-img-element -- short-lived signed URL on storage's origin */}
      <img src={url} alt={S.photo.alt} className="kt-photo-viewer__img" />
    </div>
  );
}
