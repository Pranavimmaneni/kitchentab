"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { OTPInput, OTP_LENGTH } from "@/components/OTPInput";
import { PillButton } from "@/components/PillButton";
import { ActionBar } from "@/components/screens/ActionBar";
import { BackBar } from "@/components/screens/BackBar";
import { api, isApiError } from "@/lib/api/client";
import { keys } from "@/lib/api/hooks";
import type { Me, SendCodeOut, SignedIn } from "@/lib/api/types";
import {
  afterSignIn,
  clearPending,
  loadPending,
  savePending,
  type PendingSignIn,
} from "@/lib/auth-flow";
import { strings } from "@/lib/strings";

type CodeState = "idle" | "verifying" | "expired" | "locked";

function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** B · Enter code (PRD §14A). */
export function VerifyScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<PendingSignIn | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | undefined>();
  const [state, setState] = useState<CodeState>("idle");
  const [now, setNow] = useState(() => Date.now());
  const [resending, setResending] = useState(false);
  const submitted = useRef<string | null>(null);

  useEffect(() => {
    const saved = loadPending();
    if (!saved) {
      router.replace("/start");
      return;
    }
    setPending(saved);
  }, [router]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const backHref = pending?.returnTo ?? "/start";

  const verify = useCallback(
    async (value: string) => {
      if (!pending || value.length !== OTP_LENGTH || submitted.current === value) return;
      submitted.current = value;
      setState("verifying");
      setError(undefined);
      try {
        await api.post<SignedIn>(
          "/auth/otp/verify",
          { channel: pending.channel, destination: pending.destination, code: value },
          { retries: 0 },
        );
        const me = await api.get<Me>("/me");
        queryClient.setQueryData(keys.me, me);
        clearPending();
        router.replace(afterSignIn(pending, me.houses.length));
      } catch (err) {
        submitted.current = null;
        const codeName = isApiError(err) ? err.code : "";
        if (codeName === "CODE_EXPIRED") {
          setState("expired");
          setError(strings.verify.expired);
        } else if (codeName === "CODE_LOCKED") {
          setState("locked");
          setError(strings.verify.locked);
        } else {
          setState("idle");
          setError(isApiError(err) ? err.title : strings.error.title);
        }
      }
    },
    [pending, queryClient, router],
  );

  async function resend(): Promise<void> {
    if (!pending || resending) return;
    setResending(true);
    try {
      const out = await api.post<SendCodeOut>(
        "/auth/otp/send",
        { channel: pending.channel, destination: pending.destination },
        { retries: 0 },
      );
      const next = {
        ...pending,
        masked: out.masked,
        resendAt: Date.now() + out.resend_after_seconds * 1000,
      };
      savePending(next);
      setPending(next);
      setCode("");
      setError(undefined);
      setState("idle");
      submitted.current = null;
    } catch (err) {
      if (isApiError(err) && err.status === 429) {
        const minutes = Math.max(1, Math.ceil((err.retryAfterSeconds ?? 60) / 60));
        setError(strings.start.rateLimited(minutes));
      } else {
        setError(isApiError(err) ? err.title : strings.error.title);
      }
    } finally {
      setResending(false);
    }
  }

  if (!pending) return <div className="kt-screen" aria-busy="true" />;

  const isPhone = pending.channel === "phone";
  const waitSeconds = Math.ceil((pending.resendAt - now) / 1000);
  const blocked = state === "expired" || state === "locked";

  return (
    <div className="kt-screen">
      <BackBar href={backHref} />
      <main className="kt-screen__main">
        <h1 className="kt-verify__title">{isPhone ? strings.verify.texts : strings.verify.inbox}</h1>
        <p className="kt-verify__sub">
          {strings.verify.sentTo} <strong>{pending.masked}</strong>
          {strings.verify.worksFor}
        </p>
        <OTPInput
          value={code}
          onChange={(next) => {
            setCode(next);
            if (error && state === "idle") setError(undefined);
          }}
          onComplete={(value) => void verify(value)}
          error={error}
          autoFocus
          disabled={state === "verifying" || blocked}
        />
        {blocked ? (
          <PillButton variant="secondary" size={52} onClick={() => void resend()} loading={resending}>
            {strings.verify.sendNew}
          </PillButton>
        ) : null}
        <div className="kt-resend-row">
          {waitSeconds > 0 ? (
            <span aria-live="off">{strings.verify.resendIn(clock(waitSeconds))}</span>
          ) : (
            <button type="button" className="kt-text-link" onClick={() => void resend()} disabled={resending}>
              {strings.verify.resend}
            </button>
          )}
          <a className="kt-text-link" href={pending.returnTo ?? "/start"}>
            {isPhone ? strings.verify.differentNumber : strings.verify.differentEmail}
          </a>
        </div>
      </main>
      <ActionBar>
        <PillButton
          fullWidth
          onClick={() => void verify(code)}
          disabled={code.length !== OTP_LENGTH || blocked}
          loading={state === "verifying"}
        >
          {strings.verify.cta}
        </PillButton>
        <p className="kt-footnote">{strings.otp.autofillHint}</p>
      </ActionBar>
    </div>
  );
}
