import type { Metadata } from "next";
import { ReceiptCard } from "@/components/ReceiptCard";
import { ThemeToggle } from "@/components/ThemeToggle";
import { strings } from "@/lib/strings";
import { OfflineRetry } from "./OfflineRetry";

export const metadata: Metadata = { title: strings.pwa.offline.title, robots: { index: false } };

const S = strings.pwa.offline;

/**
 * Served by the service worker when a screen was never saved on this device and there is no
 * connection (PRD §20.1, PWA-02). Precached with the app shell. Rendered per request (not static)
 * so its scripts carry the CSP nonce of the response that gets precached with them (SEC-03).
 */
export default function OfflinePage() {
  return (
    <div className="kt-screen">
      <div className="kt-topbar">
        <span className="kt-wordmark">KITCHENTAB</span>
        <ThemeToggle />
      </div>
      <main className="kt-screen__main">
        <ReceiptCard heading={S.kicker}>
          <div className="kt-offline-page">
            <h1 className="kt-offline-page__title">{S.title}</h1>
            <p className="kt-offline-page__body">{S.body}</p>
            <OfflineRetry />
          </div>
        </ReceiptCard>
      </main>
    </div>
  );
}
