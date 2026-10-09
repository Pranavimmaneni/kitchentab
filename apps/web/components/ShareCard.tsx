"use client";

import { useId } from "react";
import { strings } from "@/lib/strings";

export type ShareCardProps = {
  variant?: "bill" | "invite" | "nudge";
  /** Small line on the lime band, e.g. "KITCHENTAB · APT 4B". */
  kicker: string;
  /** Band title, e.g. "SHOPRITE · $95.18". */
  title: string;
  /** Band footer, e.g. "SPLIT 4 WAYS · TAP FOR YOUR SHARE". */
  subline?: string;
  message: string;
  onMessageChange?: (message: string) => void;
  /** Link shown under the message (display text only). */
  link: string;
};

/** Chat-bubble preview with a lime band, an editable message and the link (PRD §14B). */
export function ShareCard({
  variant = "bill",
  kicker,
  title,
  subline,
  message,
  onMessageChange,
  link,
}: ShareCardProps) {
  const messageId = useId();
  return (
    <div className="kt-share-card" data-variant={variant}>
      <div className="kt-share-card__band">
        <span className="kt-share-card__kicker">{kicker}</span>
        <span className="kt-share-card__title">{title}</span>
        {subline ? <span className="kt-share-card__kicker">{subline}</span> : null}
      </div>
      <div className="kt-share-card__body">
        <label htmlFor={messageId} className="kt-label">
          {strings.share.message}
        </label>
        <textarea
          id={messageId}
          className="kt-share-card__message"
          rows={2}
          value={message}
          onChange={(event) => onMessageChange?.(event.target.value)}
          readOnly={!onMessageChange}
        />
        <span className="kt-share-card__link">{link}</span>
      </div>
    </div>
  );
}
