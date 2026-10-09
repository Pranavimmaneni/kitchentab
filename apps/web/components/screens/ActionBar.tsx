import type { ReactNode } from "react";

/** Sticky bottom action bar on paper with a 2 px ink top border (PRD §14A global rules). */
export function ActionBar({ children }: { children: ReactNode }) {
  return <div className="kt-action-bar">{children}</div>;
}
