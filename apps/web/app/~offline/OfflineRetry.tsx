"use client";

import { PillButton } from "@/components/PillButton";
import { strings } from "@/lib/strings";

export function OfflineRetry() {
  return (
    <div className="kt-offline-page__actions">
      <PillButton size={52} fullWidth onClick={() => window.location.reload()}>
        {strings.pwa.offline.retry}
      </PillButton>
      <PillButton variant="secondary" size={52} fullWidth href="/">
        {strings.pwa.offline.home}
      </PillButton>
    </div>
  );
}
