import { appIcon } from "@/lib/app-icon";

export const dynamic = "force-static";

/** Notification badge (Android status bar): white glyph on transparent, 96 px (PRD §20.1). */
export function GET() {
  return appIcon(96, "badge");
}
