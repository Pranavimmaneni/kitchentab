"use client";

import { useEffect, useId, useRef, useState } from "react";
import { PillButton } from "@/components/PillButton";
import { Toast } from "@/components/Toast";
import type { ReadDetail } from "@/lib/api/signals";
import { track } from "@/lib/pwa/analytics";
import { hideInstallCard, promptInstall, usePrompts } from "@/lib/pwa/prompts";
import { applyUpdate, busyNow, useUpdateState } from "@/lib/pwa/register";
import { strings } from "@/lib/strings";
import { useOnline } from "@/lib/sync/online";
import { useFocusTrap } from "@/lib/use-focus-trap";

/**
 * "New version ready · Refresh" (PWA-08): appears once a new worker waits and nothing is open
 * (sheet, dialog, focused field, write in flight); never reloads by itself.
 */
export function UpdateToast() {
  const { waiting, staleClient } = useUpdateState();
  const [idle, setIdle] = useState(false);
  const [gone, setGone] = useState(false);
  useEffect(() => {
    if (!waiting) return;
    const check = () => setIdle(!busyNow());
    check();
    const timer = window.setInterval(check, 700);
    return () => window.clearInterval(timer);
  }, [waiting]);
  const show = waiting && idle && !gone && !staleClient;
  return (
    <div className="kt-update-toast" data-show={show || undefined}>
      <Toast
        message={show ? strings.pwa.updateReady : null}
        actionLabel={strings.pwa.refresh}
        duration={Number.POSITIVE_INFINITY}
        onUndo={() => void applyUpdate()}
        onDismiss={() => setGone(true)}
      />
    </div>
  );
}

/** 426 STALE_CLIENT (PWA-09, SYNC-22): the refresh prompt, shown whatever is open. */
export function StalePrompt() {
  const { staleClient } = useUpdateState();
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const bodyId = useId();
  const [busy, setBusy] = useState(false);
  useFocusTrap(ref, staleClient, () => undefined);
  if (!staleClient) return null;
  return (
    <div className="kt-dialog kt-stale-prompt">
      <div className="kt-dialog__backdrop" aria-hidden="true" />
      <div
        ref={ref}
        className="kt-dialog__card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        tabIndex={-1}
      >
        <h2 id={titleId} className="kt-dialog__title">
          {strings.pwa.staleTitle}
        </h2>
        <p id={bodyId} className="kt-dialog__body">
          {strings.pwa.staleBody}
        </p>
        <PillButton
          size={52}
          fullWidth
          loading={busy}
          onClick={() => {
            setBusy(true);
            void applyUpdate();
          }}
        >
          {strings.pwa.refresh}
        </PillButton>
      </div>
    </div>
  );
}

/**
 * The read-models came from this device's saved copy because the network took over 3 s (PWA-03).
 * Offline has its own banner ("YOU'RE OFFLINE · SHOWING SAVED TABS").
 */
export function StaleBanner() {
  const online = useOnline();
  const [stale, setStale] = useState(false);
  useEffect(() => {
    // Stale while any read model's latest answer was the saved copy; a fresh answer clears it.
    const stalePaths = new Set<string>();
    const onRead = (event: Event) => {
      const { path, stale: isStale } = (event as CustomEvent<ReadDetail>).detail;
      if (isStale) stalePaths.add(path);
      else stalePaths.delete(path);
      setStale(stalePaths.size > 0);
    };
    window.addEventListener("kt:read", onRead);
    return () => window.removeEventListener("kt:read", onRead);
  }, []);
  if (!stale || !online) return null;
  return (
    <div className="kt-offline kt-stale-banner" role="status">
      {strings.pwa.slow}
    </div>
  );
}

/** Android/desktop Chromium: "Add KitchenTab to your home screen" with Install (PWA-05). */
export function InstallCard() {
  const { installCard } = usePrompts();
  const titleId = useId();
  if (!installCard) return null;
  const S = strings.pwa.install;
  return (
    <section className="kt-install-card" aria-labelledby={titleId}>
      <p id={titleId} className="kt-install-card__title">
        {S.title}
      </p>
      <p className="kt-install-card__body">{S.body}</p>
      <div className="kt-install-card__actions">
        <PillButton
          size={44}
          variant="ink"
          onClick={() => {
            track("pwa_install_prompted");
            void promptInstall().then((outcome) => track(`pwa_install_${outcome}`));
          }}
        >
          {S.button}
        </PillButton>
        <PillButton size={44} variant="secondary" onClick={hideInstallCard}>
          {S.notNow}
        </PillButton>
      </div>
    </section>
  );
}
