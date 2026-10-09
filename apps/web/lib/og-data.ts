// Server-only: the OG image fetches the bill without the viewer's cookies (crawlers have none).
import { serverGet } from "./api/server";
import type { BillLinkView } from "./api/types";

export async function loadBillLinkPublic(token: string): Promise<BillLinkView | null> {
  try {
    return await serverGet<BillLinkView>(`/b/${encodeURIComponent(token)}`, { forwardCookies: false });
  } catch {
    return null;
  }
}
