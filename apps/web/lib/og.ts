/**
 * Link-preview card content for a bill (PRD §8 "Link previews", LINK-05): store, date, total,
 * "SPLIT N WAYS" and the house name. Never member names or per-person amounts.
 */
import type { BillLinkView } from "./api/types";
import { longDate, splitWays } from "./bills";
import { formatMoney } from "./format";
import { strings } from "./strings";

export type OgCard = {
  kicker: string;
  store: string;
  date: string;
  total: string;
  ways: string;
  house: string;
};

export function ogCard(view: Pick<BillLinkView, "bill" | "items" | "house">): OgCard {
  const n = splitWays(view);
  return {
    kicker: strings.start.receiptNo,
    store: (view.bill.store_name ?? strings.bill.untitled).toUpperCase(),
    date: longDate(view.bill.purchase_date),
    total: formatMoney(view.bill.total_cents, view.bill.currency),
    ways: `SPLIT ${n} ${n === 1 ? "WAY" : "WAYS"}`,
    house: view.house.name.toUpperCase(),
  };
}

/** The generic card for a link that no longer works. */
export const OG_EXPIRED: OgCard = {
  kicker: strings.start.receiptNo,
  store: strings.notFound.heading,
  date: "",
  total: "",
  ways: "",
  house: strings.app.name.toUpperCase(),
};
