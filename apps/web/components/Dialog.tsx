"use client";

import { useId, useRef, type ReactNode } from "react";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { strings } from "@/lib/strings";
import { PillButton } from "./PillButton";

export type DialogProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children?: ReactNode;
  variant?: "confirm" | "danger";
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  confirmDisabled?: boolean;
  confirmLoading?: boolean;
};

/** Centered receipt card, max 360 px; `role=dialog` labelled by its title (PRD §14B). */
export function Dialog({
  open,
  onClose,
  title,
  children,
  variant = "confirm",
  confirmLabel,
  cancelLabel = strings.dialog.cancel,
  onConfirm,
  confirmDisabled = false,
  confirmLoading = false,
}: DialogProps) {
  const titleId = useId();
  const bodyId = useId();
  const cardRef = useRef<HTMLDivElement>(null);
  useFocusTrap(cardRef, open, onClose);
  if (!open) return null;
  return (
    <div className="kt-dialog">
      <div className="kt-dialog__backdrop" aria-hidden="true" onClick={onClose} />
      <div
        ref={cardRef}
        className="kt-dialog__card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={children ? bodyId : undefined}
        data-variant={variant}
        tabIndex={-1}
      >
        <h2 id={titleId} className="kt-dialog__title">
          {title}
        </h2>
        {children ? (
          <div id={bodyId} className="kt-dialog__body">
            {children}
          </div>
        ) : null}
        <div className="kt-dialog__actions">
          <PillButton variant="secondary" size={52} onClick={onClose}>
            {cancelLabel}
          </PillButton>
          <PillButton
            variant={variant === "danger" ? "danger" : "primary"}
            size={52}
            onClick={onConfirm}
            disabled={confirmDisabled}
            loading={confirmLoading}
          >
            {confirmLabel}
          </PillButton>
        </div>
      </div>
    </div>
  );
}
