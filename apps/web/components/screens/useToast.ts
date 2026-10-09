"use client";

import { useCallback, useState } from "react";

export type ToastState = {
  message: string;
  variant?: "info" | "error";
  actionLabel?: string;
  onAction?: () => void;
  /** Bumps so the same message twice re-arms the timer. */
  id: number;
};

/** One toast at a time per screen (PRD §14A: ink pill above the bottom bar, 4 s). */
export function useToast() {
  const [toast, setToast] = useState<ToastState | null>(null);
  const show = useCallback((next: Omit<ToastState, "id">) => {
    setToast({ ...next, id: Date.now() });
  }, []);
  const dismiss = useCallback(() => setToast(null), []);
  return { toast, show, dismiss };
}
