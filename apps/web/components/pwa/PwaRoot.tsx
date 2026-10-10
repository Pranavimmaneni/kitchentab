"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Toast } from "@/components/Toast";
import { api } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks";
import type { WriteDetail } from "@/lib/api/signals";
import { trackOnce } from "@/lib/pwa/analytics";
import { applyBadge, isIos, isStandalone, notificationPermission, pushSupported } from "@/lib/pwa/device";
import { KEYS, countVisit, getString, readPromptMemory, rememberInstallShown, setString } from "@/lib/pwa/local";
import { decide, momentForWrite, type Moment } from "@/lib/pwa/moments";
import { captureInstallPrompt, installed, openN1, promptState, showInstallCard } from "@/lib/pwa/prompts";
import { swRegistration, syncSubscription, type EnableResult } from "@/lib/pwa/push";
import { markStaleClient, registerServiceWorker, busyNow } from "@/lib/pwa/register";
import { safePath } from "@/lib/pwa/sw-handlers";
import { strings } from "@/lib/strings";
import { NotifySheet, ShareArrow } from "./NotifySheet";
import { InstallCard, StaleBanner, StalePrompt, UpdateToast } from "./ShellPrompts";

export type Inbox = {
  items: { id: string; type: string; url: string; read: boolean }[];
  unread: number;
  badge: number;
};

export const inboxKey = ["me", "inbox"] as const;

const PUBLIC_PAGES = /^\/(start|verify|b\/|h\/|g\/|~offline|dev\/)/;

async function subscribed(): Promise<boolean> {
  if (!pushSupported() || notificationPermission() !== "granted") return false;
  const reg = await swRegistration(1500);
  return Boolean(await reg?.pushManager.getSubscription().catch(() => null));
}

/**
 * The installable-app shell (PRD §20): registers the service worker, offers updates and the 426
 * refresh prompt, captures the install prompt and logs `pwa_installed`, offers install and push at
 * the moments PRD §20.1–20.2 name, keeps this device's push subscription registered, sets the app
 * badge from the inbox, and follows notification clicks and pushes from the worker.
 */
export function PwaRoot() {
  const pathname = usePathname();
  // Signed-out screens (sign-in, link pages, the offline page) don't ask who is signed in.
  const me = useMe({ enabled: !PUBLIC_PAGES.test(pathname ?? "") });
  const signedIn = Boolean(me.data);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const pending = useRef<Moment | null>(null);
  // The 2nd-visit moment, kept until something was shown (the install prompt may come later).
  const visitPending = useRef(false);
  const signedInRef = useRef(signedIn);
  signedInRef.current = signedIn;

  const inbox = useQuery({
    queryKey: inboxKey,
    queryFn: () => api.get<Inbox>("/me/inbox"),
    enabled: signedIn,
    staleTime: 10_000,
    refetchInterval: 60_000,
    retry: false,
  });

  /** Decide now, or as soon as nothing is open on screen. */
  const offer = useCallback(async (moment: Moment) => {
    if (!signedInRef.current) return;
    const current = promptState();
    if (current.n1 || current.installCard) return;
    if (busyNow()) {
      pending.current = moment;
      return;
    }
    const decision = decide(
      moment,
      {
        ios: isIos(),
        standalone: isStandalone(),
        pushSupported: pushSupported(),
        permission: notificationPermission(),
        subscribed: await subscribed(),
        canPrompt: current.canInstall,
      },
      readPromptMemory(),
      Date.now(),
    );
    if (!decision) return;
    if (moment === "visit") visitPending.current = false;
    if (decision.kind === "install") {
      rememberInstallShown();
      showInstallCard();
      return;
    }
    if (decision.countsAsInstall) rememberInstallShown();
    openN1({ platform: decision.platform, countsAsInstall: decision.countsAsInstall, fromSettings: false });
  }, []);

  // Worker, install prompt, installed state.
  useEffect(() => {
    void registerServiceWorker();
    const onPrompt = (event: Event) => {
      captureInstallPrompt(event);
      if (visitPending.current) void offer("visit");
    };
    const onInstalled = () => {
      installed();
      trackOnce("pwa_installed");
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    if (isStandalone()) trackOnce("pwa_installed");
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, [offer]);

  // API client signals: 426 → refresh prompt; writes that are moments.
  useEffect(() => {
    const onStale = () => markStaleClient();
    const onWrite = (event: Event) => {
      const { method, path } = (event as CustomEvent<WriteDetail>).detail;
      const moment = momentForWrite(method, path);
      if (moment) void offer(moment);
    };
    window.addEventListener("kt:stale-client", onStale);
    window.addEventListener("kt:write", onWrite);
    return () => {
      window.removeEventListener("kt:stale-client", onStale);
      window.removeEventListener("kt:write", onWrite);
    };
  }, [offer]);

  // Messages from the worker: a push arrived (catch up), a notification click wants a screen.
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; url?: unknown } | null;
      if (data?.type === "kt:push") {
        void queryClient.invalidateQueries();
      } else if (data?.type === "kt:navigate") {
        router.push(safePath(data.url));
      }
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, [queryClient, router]);

  // Visits (2nd visit is an install moment) and the first launch from the home screen.
  const visitDone = useRef(false);
  useEffect(() => {
    if (!signedIn || visitDone.current) return;
    visitDone.current = true;
    const visits = countVisit();
    if (isStandalone()) {
      setString(KEYS.standaloneSignedIn, "1");
      if (!getString(KEYS.standaloneOffered)) {
        setString(KEYS.standaloneOffered, "1");
        void offer("standalone");
        return;
      }
    }
    if (visits >= 2) {
      visitPending.current = true;
      void offer("visit");
    }
  }, [signedIn, offer]);

  // Push subscription kept current on every start (PUSH-04).
  useEffect(() => {
    if (signedIn) void syncSubscription();
  }, [signedIn]);

  // A bill was just sent (the share screen) — also reached by links from elsewhere.
  useEffect(() => {
    if (signedIn && pathname && /^\/bills\/[^/]+\/share$/.test(pathname)) void offer("bill");
    if (signedIn) void inbox.refetch();
    // inbox.refetch is stable; only route changes matter here
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, signedIn, offer]);

  // An ask arrived for me: the "first ask" moment (PUSH-01). App badge from the inbox (PUSH-16).
  const askSeen = useRef(false);
  const badge = inbox.data?.badge;
  useEffect(() => {
    if (!signedIn) {
      void applyBadge(0);
      return;
    }
    if (badge !== undefined) void applyBadge(badge);
    if (!askSeen.current && inbox.data?.items.some((i) => i.type === "asks" || i.type === "answers")) {
      askSeen.current = true;
      void offer("ask");
    }
  }, [signedIn, badge, inbox.data, offer]);

  // A moment that came while a sheet or form was open waits for it to close.
  useEffect(() => {
    const timer = window.setInterval(() => {
      const moment = pending.current;
      if (moment && !busyNow()) {
        pending.current = null;
        void offer(moment);
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [offer]);

  const onResult = useCallback((result: EnableResult) => {
    const S = strings.notify;
    if (result === "granted") setNotice(S.turnedOn);
    else if (result === "denied") setNotice(S.denied);
    else if (result !== "default") setNotice(S.failed);
    void queryClient.invalidateQueries({ queryKey: ["push", "devices"] });
  }, [queryClient]);

  return (
    <>
      <StaleBanner />
      <NotifySheet onResult={onResult} />
      <InstallCard />
      <ShareArrow />
      <UpdateToast />
      <StalePrompt />
      <Toast message={notice} onDismiss={() => setNotice(null)} />
    </>
  );
}
