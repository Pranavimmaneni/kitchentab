"use client";

import { useEffect, useRef, useState } from "react";
import { strings } from "@/lib/strings";

export const TOAST_DURATION_MS = 4000;

export type ToastProps = {
  /** The message to show; null hides the toast but keeps the live region mounted. */
  message: string | null;
  variant?: "info" | "error";
  /** What screen readers hear instead of `message` (e.g. without arrows). */
  spoken?: string;
  /** Shows an action button (Undo by default). */
  onUndo?: () => void;
  /** Label of the action button, e.g. "Retry" (default "Undo"). */
  actionLabel?: string;
  /** Called after `duration` ms, or after Undo. */
  onDismiss?: () => void;
  duration?: number;
  /** "fixed" floats above the bottom bar; "inline" stays in flow (demos). */
  placement?: "fixed" | "inline";
};

/**
 * Ink pill above the bottom bar, 4 s, optional Undo, announced politely (PRD §14A).
 * The 4 s timer pauses while the pointer is over the toast or focus is inside it, and resumes
 * with the time that was left (WCAG 2.2.1 Timing Adjustable), so Undo can always be reached.
 */
export function Toast({
  message,
  spoken,
  variant = "info",
  onUndo,
  actionLabel = strings.toast.undo,
  onDismiss,
  duration = TOAST_DURATION_MS,
  placement = "fixed",
}: ToastProps) {
  const dismissRef = useRef(onDismiss);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const remaining = useRef(duration);
  const paused = hovered || focused;

  useEffect(() => {
    dismissRef.current = onDismiss;
  }, [onDismiss]);

  // A new message gets the full duration. When the toast goes away while hovered or focused,
  // no leave/blur event follows, so the pause is cleared here.
  useEffect(() => {
    remaining.current = duration;
    if (!message) {
      setHovered(false);
      setFocused(false);
    }
  }, [message, duration]);

  useEffect(() => {
    if (!message || !Number.isFinite(duration) || paused) return;
    const startedAt = Date.now();
    const timer = window.setTimeout(() => dismissRef.current?.(), Math.max(0, remaining.current));
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - startedAt));
    };
  }, [message, duration, paused]);

  return (
    <div
      className="kt-toast-region"
      data-placement={placement}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {message ? (
        <div
          className="kt-toast"
          data-variant={variant}
          data-paused={paused || undefined}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          onFocus={() => setFocused(true)}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null))
              setFocused(false);
          }}
        >
          {spoken ? (
            <>
              <span className="kt-toast__message" aria-hidden="true">
                {message}
              </span>
              <span className="sr-only">{spoken}</span>
            </>
          ) : (
            <span className="kt-toast__message">{message}</span>
          )}
          {onUndo ? (
            <button
              type="button"
              className="kt-toast__undo"
              onClick={() => {
                onUndo();
                dismissRef.current?.();
              }}
            >
              {actionLabel}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
