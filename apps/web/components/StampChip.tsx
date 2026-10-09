import type { ReactNode } from "react";

export type StampTone = "lime" | "warn" | "muted";

export type StampChipProps = {
  children: ReactNode;
  tone?: StampTone;
  /** Rotation in degrees, clamped to −4…+4 (PRD §14B). */
  rotate?: number;
  /** Large SETTLED / VOIDED overlay stamp. */
  large?: boolean;
  /** Slam in with a 200 ms rotate (only when motion is allowed). */
  animate?: boolean;
};

export function clampRotation(deg: number): number {
  if (!Number.isFinite(deg)) return 0;
  return Math.max(-4, Math.min(4, deg));
}

/** Rubber-stamp chip: mono 11 bold, padding 3/8, 2 px border. Text only, never color alone. */
export function StampChip({
  children,
  tone = "lime",
  rotate = 0,
  large = false,
  animate = false,
}: StampChipProps) {
  return (
    <span
      className="kt-stamp"
      data-tone={tone}
      data-large={large || undefined}
      data-animate={animate || undefined}
      style={{ ["--stamp-rotate" as string]: `${clampRotation(rotate)}deg` }}
    >
      {children}
    </span>
  );
}
