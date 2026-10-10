import { colorIndexOf } from "@/lib/bills";
import { memberColors } from "@/lib/members";

export type OwnerDot = { id: string; initial?: string; display_name?: string; color?: string };

/** Tiny overlapping owner avatars (13–14 px, −4 px overlap) under tiles and use-soon rows. */
export function OwnerDots({ owners, size = 13 }: { owners: readonly OwnerDot[]; size?: 13 | 14 }) {
  if (owners.length === 0) return null;
  return (
    <span className="kt-owner-dots" aria-hidden="true">
      {owners.map((o, i) => {
        const colors = memberColors(colorIndexOf({ color: o.color ?? "", sort_order: i }));
        return (
          <span
            key={o.id}
            className="kt-owner-dot"
            style={{ width: size, height: size, background: colors.fill, color: colors.ink }}
          >
            {o.initial ?? (o.display_name ?? "?").slice(0, 1).toUpperCase()}
          </span>
        );
      })}
    </span>
  );
}
