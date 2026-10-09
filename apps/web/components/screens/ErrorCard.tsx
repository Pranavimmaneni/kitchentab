"use client";

import { PillButton } from "@/components/PillButton";
import { isApiError } from "@/lib/api/client";
import { strings } from "@/lib/strings";

export type ErrorCardProps = { error?: unknown; onRetry?: () => void };

/** "Generic error" (PRD §14A): inline card, Retry, error id in mono 10 for support. */
export function ErrorCard({ error, onRetry }: ErrorCardProps) {
  const id = isApiError(error) ? error.requestId : undefined;
  return (
    <div className="kt-card kt-not-found" role="alert">
      <p className="kt-not-found__title">{strings.error.title}</p>
      {onRetry ? (
        <PillButton variant="secondary" size={52} onClick={onRetry}>
          {strings.error.retry}
        </PillButton>
      ) : null}
      {id ? <span className="kt-error-id">{strings.error.errorId(id)}</span> : null}
    </div>
  );
}
