"use client";

import { useEffect, useId, useRef, useState } from "react";
import { PillButton } from "@/components/PillButton";
import { SegmentedControl } from "@/components/SegmentedControl";
import { ThemeToggle } from "@/components/ThemeToggle";
import { XIcon } from "@/components/icons";
import { isStandalone } from "@/lib/pwa/device";
import { rememberNotNow } from "@/lib/pwa/local";
import { closeN1, showShareArrow, usePrompts } from "@/lib/pwa/prompts";
import { enablePush, type EnableResult } from "@/lib/pwa/push";
import { strings } from "@/lib/strings";
import { useFocusTrap } from "@/lib/use-focus-trap";

const S = strings.notify;

function Glyph() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="6" y="2.5" width="12" height="19" rx="2" />
      <path d="M6 9.5h12M9 5.5v2M9 12.5v4" />
    </svg>
  );
}

/**
 * N1 · Turn on notifications (Notify.dc.html, PRD §20.2): three example pings, ON IPHONE /
 * ANDROID · DESKTOP picked from the user agent. In an iPhone browser tab push isn't possible, so
 * it shows the three Home Screen steps and "Show me where Share is"; everywhere else "Turn on
 * notifications" asks the browser from that tap and subscribes. "Not now" snoozes for 14 days.
 */
export function NotifySheet({ onResult }: { onResult: (result: EnableResult) => void }) {
  const { n1 } = usePrompts();
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [platform, setPlatform] = useState<"ios" | "android">("android");
  const [busy, setBusy] = useState(false);
  const open = n1 !== null;

  useEffect(() => {
    if (n1) setPlatform(n1.platform);
  }, [n1]);

  const notNow = () => {
    if (n1 && !n1.fromSettings) rememberNotNow();
    closeN1();
  };
  useFocusTrap(ref, open, notNow);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!n1) return null;
  const steps = platform === "ios" && !isStandalone();

  async function cta(): Promise<void> {
    if (steps) {
      closeN1();
      showShareArrow(true);
      return;
    }
    setBusy(true);
    const result = await enablePush();
    setBusy(false);
    closeN1();
    onResult(result);
  }

  return (
    <div ref={ref} className="kt-n1" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <header className="kt-n1__top">
        <button type="button" className="kt-n1__close" aria-label={S.close} onClick={notNow}>
          <XIcon size={20} />
        </button>
        <span className="kt-n1__kicker">{S.kicker}</span>
        <ThemeToggle />
      </header>
      <div className="kt-n1__body">
        <div className="kt-n1__intro">
          <h2 id={titleId} className="kt-n1__title">
            {S.title}
          </h2>
          <p className="kt-n1__sub">{S.sub}</p>
        </div>
        <ul className="kt-n1__pings" aria-label={S.examples}>
          {S.pings.map((ping, i) => (
            <li key={ping.title} className="kt-n1__ping" data-i={i}>
              <span className="kt-n1__ping-icon">
                <Glyph />
              </span>
              <span className="kt-n1__ping-text">
                <span className="kt-n1__ping-meta">
                  <span>{S.brand}</span>
                  <span>{ping.when}</span>
                </span>
                <span className="kt-n1__ping-title">{ping.title}</span>
                <span className="kt-n1__ping-body">{ping.body}</span>
              </span>
            </li>
          ))}
        </ul>
        <SegmentedControl<"ios" | "android">
          label={S.platform}
          value={platform}
          onChange={setPlatform}
          options={[
            { value: "ios", label: S.ios },
            { value: "android", label: S.android },
          ]}
        />
        {steps ? (
          <div className="kt-n1__steps">
            <span className="kt-n1__steps-title">{S.iosStepsTitle}</span>
            <ol className="kt-n1__steps-list">
              {S.iosSteps.map((text, i) => (
                <li key={text}>
                  <span className="kt-n1__step-n" aria-hidden="true">
                    {i + 1}
                  </span>
                  <span>{text}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : (
          <p className="kt-n1__note">{S.androidNote}</p>
        )}
      </div>
      <div className="kt-n1__actions">
        <PillButton fullWidth loading={busy} onClick={() => void cta()}>
          {steps ? S.showShare : S.turnOn}
        </PillButton>
        <button type="button" className="kt-text-link kt-n1__later" onClick={notNow}>
          {S.notNow}
        </button>
      </div>
    </div>
  );
}

/** "Share is down here": points at Safari's toolbar after "Show me where Share is". */
export function ShareArrow() {
  const { shareArrow } = usePrompts();
  useEffect(() => {
    if (!shareArrow) return;
    const t = window.setTimeout(() => showShareArrow(false), 12_000);
    return () => window.clearTimeout(t);
  }, [shareArrow]);
  if (!shareArrow) return null;
  return (
    <button type="button" className="kt-share-arrow" onClick={() => showShareArrow(false)}>
      <span>{strings.pwa.shareArrow}</span>
      <svg
        width="28"
        height="40"
        viewBox="0 0 28 40"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M14 2v34M4 26l10 10 10-10" />
      </svg>
    </button>
  );
}
