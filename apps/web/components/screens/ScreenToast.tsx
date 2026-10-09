"use client";

import { Toast } from "@/components/Toast";
import type { ToastState } from "./useToast";

export function ScreenToast({ toast, onDismiss }: { toast: ToastState | null; onDismiss: () => void }) {
  return (
    <Toast
      message={toast?.message ?? null}
      variant={toast?.variant}
      onUndo={toast?.onAction}
      actionLabel={toast?.actionLabel}
      onDismiss={onDismiss}
    />
  );
}
