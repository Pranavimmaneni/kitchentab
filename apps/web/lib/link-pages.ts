// Server-only data loading for the link pages D (`/b/{token}`, `/h/{token}`).
import type { Metadata } from "next";
import { cache } from "react";
import { isApiError } from "./api/client";
import { publicOrigin, serverGet } from "./api/server";
import type { BillLinkView, JoinView } from "./api/types";
import { shortDate, splitWays } from "./bills";
import { formatMoney } from "./format";
import { strings } from "./strings";

export type Loaded<T> = { ok: true; data: T } | { ok: false; expired: boolean; error?: unknown };

async function load<T>(path: string): Promise<Loaded<T>> {
  try {
    return { ok: true, data: await serverGet<T>(path) };
  } catch (error) {
    if (isApiError(error) && (error.status === 410 || error.status === 404 || error.code === "LINK_EXPIRED")) {
      return { ok: false, expired: true };
    }
    return { ok: false, expired: false, error };
  }
}

/** One fetch per request, shared by generateMetadata and the page. */
export const loadBillLink = cache((token: string) => load<BillLinkView>(`/b/${encodeURIComponent(token)}`));
export const loadInvite = cache((token: string) => load<JoinView>(`/join/${encodeURIComponent(token)}`));

/** Link pages are never indexed and never leak the URL as a referrer (PRD §8, LINK-04). */
export const LINK_METADATA: Metadata = {
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  referrer: "no-referrer",
};

/** Bill link preview text: store, date, total, ways, house. Never names or per-person amounts. */
export function billPreviewText(view: BillLinkView): { title: string; description: string } {
  const store = view.bill.store_name ?? strings.bill.untitled;
  const total = formatMoney(view.bill.total_cents, view.bill.currency);
  return {
    title: `${store} · ${total}`,
    description: `${shortDate(view.bill.purchase_date)} · SPLIT ${splitWays(view)} WAYS · ${view.house.name.toUpperCase()}`,
  };
}

export async function billLinkMetadata(token: string): Promise<Metadata> {
  const loaded = await loadBillLink(token);
  const base: Metadata = { ...LINK_METADATA, metadataBase: new URL(await publicOrigin()) };
  if (!loaded.ok) return { ...base, title: strings.notFound.heading };
  const { title, description } = billPreviewText(loaded.data);
  return { ...base, title, description, openGraph: { title, description, type: "website" } };
}
