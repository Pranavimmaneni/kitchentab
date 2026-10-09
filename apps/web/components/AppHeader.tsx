import Link from "next/link";
import type { ReactNode } from "react";
import { strings } from "@/lib/strings";
import { BackIcon, MoreIcon } from "./icons";
import { ThemeToggle } from "./ThemeToggle";
import type { Theme } from "@/lib/theme";

export type AppHeaderProps = {
  title: string;
  /** Step label above the title, e.g. "STEP 2/3 · SHOPRITE · 10/02" (mono 11, muted). */
  stepLabel?: string;
  /** Line under the title, e.g. "10/02 · YOU PAID · $95.18". */
  subtitle?: string;
  back?: { href: string; label?: string };
  /** Shows a 44 px ⋯ button. */
  onMore?: () => void;
  moreLabel?: string;
  /** Extra actions before the theme toggle (e.g. the Kitchen scan button). */
  actions?: ReactNode;
  /** Large title for top-level screens (Kitchen, Tabs): mono 26 instead of 20. */
  large?: boolean;
  headingLevel?: 1 | 2 | 3;
  /** Demo only: render the toggle as it looks in this theme. */
  previewTheme?: Theme;
  /** Extra content under the title row (progress bar, buttons). */
  children?: ReactNode;
};

/** 44 px back · title block · 44 px theme toggle; top padding 52 px + safe area (PRD §14A). */
export function AppHeader({
  title,
  stepLabel,
  subtitle,
  back,
  onMore,
  moreLabel = strings.header.more,
  actions,
  large = false,
  headingLevel = 1,
  previewTheme,
  children,
}: AppHeaderProps) {
  const Heading = `h${headingLevel}` as "h1" | "h2" | "h3";
  return (
    <header
      className="kt-app-header"
      data-large={large || undefined}
      data-back={Boolean(back) || undefined}
    >
      <div className="kt-app-header__row">
        {back ? (
          <Link
            href={back.href}
            aria-label={back.label ?? strings.header.back}
            className="kt-app-header__back"
          >
            <BackIcon size={22} />
          </Link>
        ) : null}
        <div className="kt-app-header__titles">
          {stepLabel ? <span className="kt-app-header__step">{stepLabel}</span> : null}
          <Heading className="kt-app-header__title">{title}</Heading>
          {subtitle ? <span className="kt-app-header__step">{subtitle}</span> : null}
        </div>
        {actions}
        {onMore ? (
          <button type="button" className="kt-icon-button" aria-label={moreLabel} onClick={onMore}>
            <MoreIcon size={20} />
          </button>
        ) : null}
        <ThemeToggle previewTheme={previewTheme} />
      </div>
      {children ? <div className="kt-app-header__extra">{children}</div> : null}
    </header>
  );
}
