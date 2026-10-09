import { ImageResponse } from "next/og";

const LIME = "#C6F432";
const INK = "#121210";

export type AppIconKind = "any" | "maskable" | "apple";

/**
 * KitchenTab app icon: a lime rounded square with the ink fridge glyph from N1 (PRD §20.1).
 * - any: rounded square on a transparent canvas
 * - maskable: full-bleed lime, glyph inside the 80% safe zone
 * - apple: full-bleed lime square (iOS applies its own mask)
 */
export function appIcon(size: number, kind: AppIconKind): ImageResponse {
  const fullBleed = kind !== "any";
  const glyph = Math.round(size * (kind === "maskable" ? 0.5 : 0.6));
  const border = Math.max(2, Math.round(size / 48));
  return new ImageResponse(
    (
      <div
        style={{
          width: size,
          height: size,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: fullBleed ? LIME : "transparent",
        }}
      >
        <div
          style={{
            width: fullBleed ? size : Math.round(size * 0.92),
            height: fullBleed ? size : Math.round(size * 0.92),
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: LIME,
            borderRadius: fullBleed ? 0 : Math.round(size * 0.22),
            border: fullBleed ? "none" : `${border}px solid ${INK}`,
          }}
        >
          <svg
            width={glyph}
            height={glyph}
            viewBox="0 0 24 24"
            fill="none"
            stroke={INK}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="6" y="2.5" width="12" height="19" rx="2" />
            <path d="M6 9.5h12M9 5.5v2M9 12.5v4" />
          </svg>
        </div>
      </div>
    ),
    { width: size, height: size },
  );
}
