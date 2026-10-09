import { ImageResponse } from "next/og";
import { loadBillLinkPublic } from "@/lib/og-data";
import { OG_EXPIRED, ogCard } from "@/lib/og";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Grocery tab on KitchenTab";
export const dynamic = "force-dynamic";

// Receipt palette (light theme tokens, PRD §14); an image can't read CSS custom properties.
const BG = "#ECEAE2";
const PAPER = "#FBFAF6";
const INK = "#121210";
const MUTED = "#5A5850";
const LIME = "#C6F432";

/** 1200×630 receipt-style preview for a bill link (LINK-05). No names or per-person amounts. */
export default async function Image({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const view = await loadBillLinkPublic(token);
  const card = view ? ogCard(view) : OG_EXPIRED;
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: BG,
          fontFamily: "monospace",
        }}
      >
        <div
          style={{
            width: 1000,
            display: "flex",
            flexDirection: "column",
            background: PAPER,
            border: `4px solid ${INK}`,
            boxShadow: `12px 12px 0 ${INK}`,
            padding: "48px 56px",
            transform: "rotate(-1deg)",
            color: INK,
          }}
        >
          <div style={{ display: "flex", fontSize: 28, color: MUTED }}>{card.kicker}</div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              marginTop: 24,
              fontSize: 72,
              fontWeight: 700,
            }}
          >
            <span>{card.store}</span>
            <span>{card.total}</span>
          </div>
          <div style={{ display: "flex", marginTop: 16, fontSize: 32, color: MUTED }}>
            {[card.date, card.house].filter(Boolean).join(" · ")}
          </div>
          <div style={{ display: "flex", borderTop: `4px dashed ${MUTED}`, marginTop: 32, paddingTop: 32 }}>
            {card.ways ? (
              <span style={{ background: LIME, border: `4px solid ${INK}`, padding: "8px 20px", fontSize: 40, fontWeight: 700 }}>
                {card.ways}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    ),
    { ...size },
  );
}
