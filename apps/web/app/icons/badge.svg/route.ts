import { GLYPH_SVG } from "@/lib/app-icon";

export const dynamic = "force-static";

/** Monochrome manifest icon (`purpose: "monochrome"`, PRD §20.1): the browser tints it. */
export function GET() {
  return new Response(GLYPH_SVG("#000000"), {
    headers: { "Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400" },
  });
}
