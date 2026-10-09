import { useId, type ReactNode } from "react";

export type ReceiptCardProps = {
  /** Section heading (mono 11 muted label, like "WHO OWES WHAT"). */
  heading: string;
  headingLevel?: 2 | 3 | 4;
  /** Visually hide the heading but keep it for screen readers. */
  hideHeading?: boolean;
  /** Tilt the card −1° (hero cards). */
  rotated?: boolean;
  /** Stamp pinned to the top-right corner, e.g. a StampChip "STILL OPEN · DAY 2". */
  stamp?: ReactNode;
  /** Large overlay stamp centered over the card (SETTLED / VOIDED). */
  overlay?: ReactNode;
  /** 5 px offset shadow; on by default (hero cards). */
  shadow?: boolean;
  muted?: boolean;
  children: ReactNode;
};

/** Paper card, 2 px border, radius 4, 5 px offset shadow, padding 16–18 (PRD §14B). */
export function ReceiptCard({
  heading,
  headingLevel = 2,
  hideHeading = false,
  rotated = false,
  stamp,
  overlay,
  shadow = true,
  muted = false,
  children,
}: ReceiptCardProps) {
  const headingId = useId();
  const Heading = `h${headingLevel}` as "h2" | "h3" | "h4";
  return (
    <section
      className="kt-receipt"
      aria-labelledby={headingId}
      data-rotated={rotated || undefined}
      data-shadow={shadow || undefined}
      data-muted={muted || undefined}
      data-stamped={Boolean(stamp) || undefined}
    >
      {stamp ? <div className="kt-receipt__stamp">{stamp}</div> : null}
      <Heading id={headingId} className={hideHeading ? "sr-only" : "kt-receipt__heading"}>
        {heading}
      </Heading>
      <div className="kt-receipt__body">{children}</div>
      {overlay ? <div className="kt-receipt__overlay">{overlay}</div> : null}
    </section>
  );
}
