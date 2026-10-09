import type { ReactNode } from "react";

export type EmptyStateProps = {
  title: string;
  body?: string;
  action?: ReactNode;
  headingLevel?: 2 | 3 | 4;
};

/** Torn receipt illustration (inline SVG), title and an optional action (PRD §14B). */
export function EmptyState({ title, body, action, headingLevel = 2 }: EmptyStateProps) {
  const Heading = `h${headingLevel}` as "h2" | "h3" | "h4";
  return (
    <div className="kt-empty">
      <svg className="kt-empty__art" width="96" height="112" viewBox="0 0 96 112" fill="none" aria-hidden="true" focusable="false">
        <path
          d="M14 6h68v92l-8.5-6-8.5 6-8.5-6-8.5 6-8.5-6-8.5 6-8.5-6-8.5 6z"
          fill="var(--paper)"
          stroke="var(--ink)"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path d="M26 26h44M26 40h30M26 54h38" stroke="var(--faint)" strokeWidth="2" strokeLinecap="round" strokeDasharray="4 4" />
        <rect x="26" y="68" width="44" height="12" fill="var(--accent)" stroke="var(--ink)" strokeWidth="2" />
      </svg>
      <Heading className="kt-empty__title">{title}</Heading>
      {body ? <p className="kt-empty__body">{body}</p> : null}
      {action}
    </div>
  );
}
