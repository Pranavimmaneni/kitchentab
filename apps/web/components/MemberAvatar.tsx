import { initialOf, memberColors } from "@/lib/members";

export type AvatarSize = 26 | 28 | 30 | 32 | 36;

export type MemberAvatarProps = {
  name: string;
  /** 0-based join order; picks the member color (cycles after 8). */
  colorIndex: number;
  initial?: string;
  size?: AvatarSize;
  /** Placeholder member (not joined yet): dashed border, paper fill. */
  placeholder?: boolean;
  /** Hide from assistive tech because the name is right next to it. */
  decorative?: boolean;
};

/** Circle with a 2 px ink border and the member's initial in Space Mono (PRD §14B). */
export function MemberAvatar({
  name,
  colorIndex,
  initial,
  size = 30,
  placeholder = false,
  decorative = true,
}: MemberAvatarProps) {
  const colors = memberColors(colorIndex);
  return (
    <span
      className="kt-avatar"
      data-placeholder={placeholder || undefined}
      aria-hidden={decorative || undefined}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : name}
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.44),
        background: placeholder ? undefined : colors.fill,
        color: placeholder ? undefined : colors.ink,
      }}
    >
      {initial ?? initialOf(name)}
    </span>
  );
}
