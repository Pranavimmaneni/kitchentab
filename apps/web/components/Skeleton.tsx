import { strings } from "@/lib/strings";

const WIDTHS = ["60%", "80%", "40%"] as const;

export type SkeletonProps = {
  variant?: "lines" | "card";
  lines?: number;
  label?: string;
};

/** Dashed receipt lines at 60/80/40% widths; shimmer only when motion is allowed. */
export function Skeleton({ variant = "lines", lines = 3, label = strings.skeleton.loading }: SkeletonProps) {
  return (
    <div className="kt-skeleton" data-variant={variant} aria-busy="true" role="status">
      <span className="sr-only">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="kt-skeleton__line" style={{ width: WIDTHS[i % WIDTHS.length] }} aria-hidden="true" />
      ))}
    </div>
  );
}
