"use client";

import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { PillButton } from "@/components/PillButton";
import { SegmentedControl } from "@/components/SegmentedControl";
import { api, isApiError } from "@/lib/api/client";
import type { Channel, SendCodeOut } from "@/lib/api/types";
import {
  formatUsPhone,
  looksLikeEmail,
  looksLikeUsPhone,
  savePending,
} from "@/lib/auth-flow";
import { strings } from "@/lib/strings";

export type CodeRequestFormProps = {
  /** Channels configured on the server (`GET /auth/methods`). Email is always on. */
  phoneEnabled: boolean;
  /** Show the EMAIL / PHONE switch (A); D passes a fixed channel instead. */
  channel?: Channel;
  /** Path to return to after verifying (D: the link page with the pending claim). */
  returnTo?: string;
  autoFocus?: boolean;
};

/** Field + "Send me a code →" (PRD §14A A); sends the code and moves on to /verify. */
export function CodeRequestForm({
  phoneEnabled,
  channel: fixedChannel,
  returnTo,
  autoFocus = false,
}: CodeRequestFormProps) {
  const router = useRouter();
  const fieldId = useId();
  const errorId = useId();
  const [channel, setChannel] = useState<Channel>(fixedChannel ?? "email");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const isPhone = channel === "phone";

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (sending) return;
    const destination = value.trim();
    if (isPhone ? !looksLikeUsPhone(destination) : !looksLikeEmail(destination)) {
      setError(isPhone ? strings.start.invalidPhone : strings.start.invalidEmail);
      return;
    }
    setError(null);
    setSending(true);
    try {
      // Sign-in sends a message: never retried automatically.
      const out = await api.post<SendCodeOut>(
        "/auth/otp/send",
        { channel, destination },
        { retries: 0 },
      );
      savePending({
        channel,
        destination,
        masked: out.masked,
        resendAt: Date.now() + out.resend_after_seconds * 1000,
        returnTo,
      });
      router.push("/verify");
    } catch (err) {
      setSending(false);
      if (isApiError(err) && err.status === 429) {
        const minutes = Math.max(1, Math.ceil((err.retryAfterSeconds ?? 60) / 60));
        setError(strings.start.rateLimited(minutes));
      } else {
        setError(isApiError(err) ? err.title : strings.error.title);
      }
    }
  }

  return (
    <form className="kt-form" onSubmit={onSubmit} noValidate>
      {phoneEnabled && !fixedChannel ? (
        <div className="kt-segmented-wrap">
          <SegmentedControl
            label={strings.start.methodLabel}
            value={channel}
            onChange={(next) => {
              setChannel(next);
              setValue("");
              setError(null);
            }}
            options={[
              { value: "email", label: strings.start.email },
              { value: "phone", label: strings.start.phone },
            ]}
          />
        </div>
      ) : null}
      <div className="kt-field">
        <label htmlFor={fieldId} className="kt-field__label">
          {isPhone ? strings.start.phoneLabel : strings.start.emailLabel}
        </label>
        <input
          id={fieldId}
          className="kt-input kt-input--big"
          type={isPhone ? "tel" : "email"}
          name={isPhone ? "phone" : "email"}
          autoComplete={isPhone ? "tel" : "email"}
          inputMode={isPhone ? "tel" : "email"}
          placeholder={isPhone ? strings.start.phonePlaceholder : strings.start.emailPlaceholder}
          value={value}
          autoFocus={autoFocus}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) =>
            setValue(isPhone ? formatUsPhone(event.target.value) : event.target.value)
          }
        />
        {error ? (
          <p id={errorId} className="kt-field-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <PillButton type="submit" fullWidth disabled={sending}>
        {sending ? strings.start.sending : strings.start.cta}
      </PillButton>
    </form>
  );
}
