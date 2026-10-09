"use client";

import { useId, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { strings } from "@/lib/strings";

export const OTP_LENGTH = 6;

export type OTPInputProps = {
  value: string;
  onChange: (value: string) => void;
  /** Called once all six digits are present. */
  onComplete?: (code: string) => void;
  /** Error message under the boxes; turns the boxes warn and shakes them once. */
  error?: string;
  label?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  name?: string;
};

function digitsOnly(text: string): string {
  return text.replace(/\D/g, "").slice(0, OTP_LENGTH);
}

/**
 * Six 60 px boxes drawn over one real input with autocomplete="one-time-code", so paste and
 * SMS/email autofill fill every box at once (PRD §14B).
 */
export function OTPInput({
  value,
  onChange,
  onComplete,
  error,
  label = strings.otp.label,
  disabled = false,
  autoFocus = false,
  name = "code",
}: OTPInputProps) {
  const errorId = useId();
  const [focused, setFocused] = useState(false);
  const code = digitsOnly(value);
  const activeIndex = Math.min(code.length, OTP_LENGTH - 1);

  function commit(next: string): void {
    const clean = digitsOnly(next);
    if (clean === code) return;
    onChange(clean);
    if (clean.length === OTP_LENGTH) onComplete?.(clean);
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>): void {
    event.preventDefault();
    commit(event.clipboardData.getData("text"));
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Backspace") {
      event.preventDefault();
      commit(code.slice(0, -1));
    }
  }

  return (
    <div className="kt-otp" data-error={error ? true : undefined}>
      <div className="kt-otp__boxes" aria-hidden="true">
        {Array.from({ length: OTP_LENGTH }, (_, i) => {
          const digit = code[i] ?? "";
          const state =
            focused && i === activeIndex && code.length < OTP_LENGTH
              ? "active"
              : digit
                ? "filled"
                : "empty";
          return (
            <span key={i} className="kt-otp__box" data-state={state}>
              {digit}
            </span>
          );
        })}
      </div>
      <input
        className="kt-otp__input"
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={OTP_LENGTH}
        name={name}
        aria-label={label}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        disabled={disabled}
        autoFocus={autoFocus}
        value={code}
        onChange={(event) => commit(event.target.value)}
        onPaste={onPaste}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
      {error ? (
        <p id={errorId} className="kt-field-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
