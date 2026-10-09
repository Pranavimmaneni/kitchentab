"use client";

import Link from "next/link";
import { useId, type MouseEvent, type ReactNode } from "react";
import { strings } from "@/lib/strings";

export type PillVariant = "primary" | "ink" | "secondary" | "dashed" | "danger";
export type PillSize = 44 | 52 | 56;

export type PillButtonProps = {
  children: ReactNode;
  variant?: PillVariant;
  /** Minimum height in px: 44 (dashed, small), 52, 56 (primary actions). */
  size?: PillSize;
  /** Renders a link instead of a button (ignored while disabled or loading). */
  href?: string;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  type?: "button" | "submit";
  disabled?: boolean;
  /** One-line reason shown under a disabled button and linked with aria-describedby. */
  disabledReason?: string;
  loading?: boolean;
  /** Forces the pressed look (for demos and toggles held down). */
  pressed?: boolean;
  icon?: ReactNode;
  fullWidth?: boolean;
  className?: string;
  "aria-label"?: string;
};

/** Pill button (PRD §14A/§14B): primary, ink, secondary, dashed, danger. */
export function PillButton({
  children,
  variant = "primary",
  size = variant === "dashed" ? 44 : 56,
  href,
  onClick,
  type = "button",
  disabled = false,
  disabledReason,
  loading = false,
  pressed = false,
  icon,
  fullWidth = false,
  className,
  "aria-label": ariaLabel,
}: PillButtonProps) {
  const reasonId = useId();
  const classes = ["kt-pill", className].filter(Boolean).join(" ");
  const data = {
    "data-variant": variant,
    "data-size": size,
    "data-pressed": pressed || undefined,
    "data-full": fullWidth || undefined,
    "data-loading": loading || undefined,
  };
  const content = (
    <>
      {loading ? <span className="kt-pill__spinner" aria-hidden="true" /> : icon}
      <span>{children}</span>
      {loading ? <span className="sr-only">{strings.button.loading}</span> : null}
    </>
  );

  let control: ReactNode;
  if (href && !disabled && !loading) {
    control = (
      <Link href={href} className={classes} aria-label={ariaLabel} {...data}>
        {content}
      </Link>
    );
  } else {
    control = (
      <button
        type={type}
        className={classes}
        disabled={disabled}
        aria-busy={loading || undefined}
        aria-disabled={loading || undefined}
        aria-describedby={disabled && disabledReason ? reasonId : undefined}
        aria-label={ariaLabel}
        onClick={(event) => {
          if (loading) {
            event.preventDefault();
            return;
          }
          onClick?.(event);
        }}
        {...data}
      >
        {content}
      </button>
    );
  }

  if (!disabled || !disabledReason) return control;
  return (
    <span className="kt-pill-wrap" data-full={fullWidth || undefined}>
      {control}
      <span id={reasonId} className="kt-pill__reason">
        {disabledReason}
      </span>
    </span>
  );
}
