"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type DragEvent } from "react";
import { AppHeader } from "@/components/AppHeader";
import { BottomSheet } from "@/components/BottomSheet";
import { PillButton } from "@/components/PillButton";
import { PencilIcon, ReceiptIcon, ScanIcon, UploadIcon } from "@/components/icons";
import { ErrorCard } from "@/components/screens/ErrorCard";
import { api, newId } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import { actionKey, clearActionKey } from "@/lib/auth-flow";
import { ACCEPT_ANY, ACCEPT_PDF, ACCEPT_PHOTO } from "@/lib/receipts/files";
import { checkPick } from "@/lib/receipts/pick";
import { startScan } from "@/lib/receipts/scan-job";
import { strings } from "@/lib/strings";
import { useOnline } from "@/lib/sync/online";

/**
 * Capture sheet (PRD §14A 1, BUILD_ALL C1): camera, photo library, "Upload PDF", drag-and-drop on
 * desktop, or manual entry. A good pick creates the draft, starts the scan in the background and
 * goes straight to the review screen, which shows "READING YOUR RECEIPT…" until it's done.
 */
export function CaptureScreen({ houseParam }: { houseParam: string | null }) {
  const router = useRouter();
  const me = useMe();
  const online = useOnline();
  const [open, setOpen] = useState(true);
  const [creating, setCreating] = useState<"manual" | "scan" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const camera = useRef<HTMLInputElement>(null);
  const library = useRef<HTMLInputElement>(null);
  const pdf = useRef<HTMLInputElement>(null);
  const houseId =
    houseParam && me.data?.houses.some((h) => h.id === houseParam)
      ? houseParam
      : (me.data?.houses[0]?.id ?? null);

  async function createDraft(): Promise<string | null> {
    if (!houseId) return null;
    const keyName = `new-bill:${houseId}`;
    const bill = await api.post<{ id: string }>(
      `/houses/${houseId}/bills`,
      {},
      {
        idempotencyKey: actionKey(keyName, newId),
      },
    );
    clearActionKey(keyName);
    return bill.id;
  }

  async function manual(): Promise<void> {
    if (creating) return;
    setCreating("manual");
    setError(null);
    try {
      const id = await createDraft();
      if (id) router.replace(`/bills/${id}/review`);
      else setCreating(null);
    } catch (err) {
      setCreating(null);
      setError(err);
    }
  }

  async function scan(files: File[]): Promise<void> {
    if (creating || files.length === 0) return;
    setRefusal(null);
    setError(null);
    setCreating("scan");
    const picked = await checkPick(files);
    if (typeof picked === "string") {
      // Refused on the device: no draft, nothing uploaded.
      setRefusal(picked);
      setCreating(null);
      return;
    }
    try {
      const id = await createDraft();
      if (!id) {
        setCreating(null);
        return;
      }
      startScan(id, picked);
      router.replace(`/bills/${id}/review`);
    } catch (err) {
      setCreating(null);
      setError(err);
    }
  }

  function picked(input: HTMLInputElement | null): void {
    const files = input?.files ? Array.from(input.files) : [];
    if (input) input.value = "";
    void scan(files);
  }

  // Drag-and-drop anywhere on the screen (desktop); the dashed zone says where.
  const scanRef = useRef(scan);
  scanRef.current = scan;
  useEffect(() => {
    function over(event: globalThis.DragEvent): void {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      setDragging(true);
    }
    function leave(event: globalThis.DragEvent): void {
      if (event.relatedTarget === null) setDragging(false);
    }
    function drop(event: globalThis.DragEvent): void {
      if (!event.dataTransfer?.files.length) return;
      event.preventDefault();
      setDragging(false);
      void scanRef.current(Array.from(event.dataTransfer.files));
    }
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, []);

  // Marks the pickers live (e2e waits for it: a change event before hydration would be lost).
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);

  const disabled = !houseId || !online || creating !== null;
  const reason = online ? undefined : strings.button.needsConnection;

  return (
    <div className="kt-screen" data-kt-capture-ready={ready && houseId ? "" : undefined}>
      <AppHeader title={strings.capture.title} back={{ href: "/" }} />
      <main className="kt-screen__main">
        {error ? <ErrorCard error={error} onRetry={() => setError(null)} /> : null}
        {!open ? (
          <PillButton fullWidth onClick={() => setOpen(true)} icon={<ScanIcon size={20} />}>
            {strings.landing.snap}
          </PillButton>
        ) : null}
      </main>
      <input
        ref={camera}
        type="file"
        accept={ACCEPT_PHOTO}
        capture="environment"
        hidden
        data-kt-input="camera"
        onChange={(e) => picked(e.currentTarget)}
      />
      <input
        ref={library}
        type="file"
        accept={ACCEPT_ANY}
        multiple
        hidden
        data-kt-input="library"
        onChange={(e) => picked(e.currentTarget)}
      />
      <input
        ref={pdf}
        type="file"
        accept={ACCEPT_PDF}
        hidden
        data-kt-input="pdf"
        onChange={(e) => picked(e.currentTarget)}
      />
      <BottomSheet open={open} onClose={() => setOpen(false)} title={strings.capture.title}>
        <div className="kt-form">
          <PillButton
            variant="secondary"
            size={52}
            fullWidth
            icon={<ScanIcon size={20} />}
            disabled={disabled}
            disabledReason={reason}
            loading={creating === "scan"}
            onClick={() => camera.current?.click()}
          >
            {strings.capture.photo}
          </PillButton>
          <PillButton
            variant="secondary"
            size={52}
            fullWidth
            icon={<UploadIcon size={20} />}
            disabled={disabled}
            onClick={() => library.current?.click()}
          >
            {strings.capture.upload}
          </PillButton>
          <PillButton
            variant="secondary"
            size={52}
            fullWidth
            icon={<ReceiptIcon size={20} />}
            disabled={disabled}
            onClick={() => pdf.current?.click()}
          >
            {strings.capture.pdf}
          </PillButton>
          <PillButton
            variant="secondary"
            size={52}
            fullWidth
            icon={<PencilIcon size={20} />}
            loading={creating === "manual"}
            disabled={disabled}
            onClick={() => void manual()}
          >
            {strings.capture.manual}
          </PillButton>
          <div
            className="kt-dropzone"
            data-active={dragging || undefined}
            onDragOver={(e: DragEvent) => e.preventDefault()}
          >
            {dragging ? strings.capture.dropActive : strings.capture.drop}
          </div>
          {creating === "scan" ? (
            <p className="kt-capture__status" role="status">
              {strings.capture.checking}
            </p>
          ) : null}
          {refusal ? (
            <p className="kt-capture__error" role="alert">
              {refusal}
            </p>
          ) : null}
        </div>
      </BottomSheet>
    </div>
  );
}
