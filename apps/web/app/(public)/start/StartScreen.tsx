"use client";

import { ReceiptCard } from "@/components/ReceiptCard";
import { ThemeToggle } from "@/components/ThemeToggle";
import { CodeRequestForm } from "@/components/screens/CodeRequestForm";
import { GoogleButton } from "@/components/screens/GoogleButton";
import type { AuthMethods } from "@/lib/api/types";
import { strings } from "@/lib/strings";

export function StartScreen({ methods }: { methods: AuthMethods }) {
  return (
    <div className="kt-screen">
      <div className="kt-topbar">
        <span className="kt-wordmark">{strings.start.wordmark}</span>
        <ThemeToggle />
      </div>
      <main className="kt-screen__main">
        <ReceiptCard heading={strings.start.receiptNo} rotated>
          <div className="kt-hero">
            <h1 className="kt-hero__headline">
              {strings.start.headline1}
              <br />
              <span className="kt-highlight">{strings.start.headline2}</span>
            </h1>
            <p className="kt-hero__sub">{strings.start.sub}</p>
          </div>
        </ReceiptCard>
        {methods.google ? (
          <>
            <GoogleButton returnTo="/" />
            <div className="kt-or">{strings.start.divider}</div>
          </>
        ) : null}
        <CodeRequestForm phoneEnabled={methods.phone} />
        <p className="kt-footnote">{strings.start.footnote}</p>
      </main>
    </div>
  );
}
