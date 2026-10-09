"use client";

import { useEffect, useRef } from "react";
import { strings } from "@/lib/strings";

export const TOAST_DURATION_MS = 4000;

export type ToastProps = {
  /** The message to show; null hides the toast but keeps the live region mounted. */
  message: string | null;
  variant?: "info" | "error";
  /** Shows an Undo action. */
  onUndo?: () => void;
  /** Called after `duration` ms, or after Undo. */
  onDismiss?: () => void;
  duration?: number;
  /** "fixed" floats above the bottom bar; "inline" stays in flow (demos). */
  placement?: "fixed" | "inline";
};

/** Ink pill above the bottom bar, 4 s, optional Undo, announced politely (PRD §14A). */
export function Toast({
  message,
  variant = "info",
  onUndo,
  onDismiss,
  duration = TOAST_DURATION_MS,
  placement = "fixed",
}: ToastProps) {
  const dismissRef = useRef(onDismiss);
  useEffect(() => {
    dismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (!message || !Number.isFinite(duration)) return;
    const timer = window.setTimeout(() => dismissRef.current?.(), duration);
    return () => window.clearTimeout(timer);
  }, [message, duration]);

  return (
    <div
      className="kt-toast-region"
      data-placement={placement}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {message ? (
        <div className="kt-toast" data-variant={variant}>
          <span className="kt-toast__message">{message}</span>
          {onUndo ? (
            <button
              type="button"
              className="kt-toast__undo"
              onClick={() => {
                onUndo();
                dismissRef.current?.();
              }}
            >
              {strings.toast.undo}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
