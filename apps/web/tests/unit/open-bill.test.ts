import { describe, expect, it } from "vitest";
import type { ActivityEntry, BillMoney, Payment } from "../../lib/api/bill-life";
import { ApiError } from "../../lib/api/client";
import type { BillView } from "../../lib/api/types";
import {
  activityLine,
  agoText,
  canReopen,
  dayOpen,
  formatWhen,
  owesRows,
  payLinks,
  problemText,
  remindedRecently,
  signedMoney,
} from "../../lib/open-bill";
import { billView } from "./fixtures/bill";

const NONE = { venmo: null, cashapp: null, paypal: null, zelle: null };

function view(): BillView {
  const base = billView();
  return {
    ...base,
    bill: { ...base.bill, state: "open", sent_at: "2026-10-02T23:31:00Z", total_cents: 3000 },
    participants: ["pranav", "arjun", "meera", "kabir"].map((id, i) => ({
      key: `m:${id}`,
      kind: "member" as const,
      id,
      display_name: id[0]!.toUpperCase() + id.slice(1),
      initial: id[0]!.toUpperCase(),
      color: "#C6F432",
      sort_order: i,
      status: "active" as const,
      excluded: false,
      claimed: true,
    })),
    shares: { "m:pranav": 1000, "m:arjun": 1000, "m:meera": 500, "m:kabir": 500 },
    me: { member_id: "arjun", is_payer: false, can_edit: false },
  };
}

function payment(partial: Partial<Payment>): Payment {
  return {
    id: "p1",
    participant: "m:meera",
    direction: "to_payer",
    amount_cents: 500,
    method: "venmo",
    note: null,
    status: "pending_confirmation",
    reject_note: null,
    version: 1,
    created_at: "2026-10-03T10:00:00Z",
    confirmed_at: null,
    ...partial,
  };
}

describe("screen 5 helpers", () => {
  it("PAY-01 who-owes rows: payer first, unpaid, pending, paid and credit from server owed amounts [FR-36] [FR-37]", () => {
    const money: BillMoney = {
      payments: [
        payment({ id: "p1", participant: "m:meera", status: "pending_confirmation", amount_cents: 500 }),
        payment({ id: "p2", participant: "m:kabir", status: "confirmed", amount_cents: 700 }),
      ],
      owed: { "m:arjun": 1000, "m:meera": 500, "m:kabir": -200 },
      changed: null,
      version: 5,
      payer_handles: NONE,
    };
    const rows = owesRows(view(), money);
    expect(rows.map((r) => [r.key, r.status, r.statusAmountCents])).toEqual([
      ["m:pranav", "payer", 1000],
      ["m:arjun", "unpaid", 1000],
      ["m:meera", "pending", 500],
      ["m:kabir", "credit", 200],
    ]);
    expect(rows.find((r) => r.key === "m:meera")!.pending!.id).toBe("p1");
    expect(rows.find((r) => r.key === "m:arjun")!.isMe).toBe(true);
    expect(rows.find((r) => r.key === "m:kabir")!.confirmedCents).toBe(700);
  });

  it("PAY-03 a confirmed payment that squares the row shows PAID [FR-38]", () => {
    const money: BillMoney = {
      payments: [payment({ participant: "m:arjun", status: "confirmed", amount_cents: 1000, method: "cash" })],
      owed: { "m:arjun": 0, "m:meera": 500, "m:kabir": 500 },
      changed: null,
      version: 5,
      payer_handles: NONE,
    };
    const arjun = owesRows(view(), money).find((r) => r.key === "m:arjun")!;
    expect(arjun.status).toBe("paid");
    expect(arjun.statusAmountCents).toBe(1000);
  });

  it("PAY-04 deep links prefill recipient and amount (Venmo app link with web fallback, Cash App, PayPal) [FR-36]", () => {
    const links = payLinks({ venmo: "pranav-r", cashapp: "$pranavr", paypal: "pranavr", zelle: "p@example.com" }, 3391, "ShopRite 10/02");
    expect(links).toEqual([
      {
        method: "venmo",
        label: "Venmo",
        href: "venmo://paycharge?txn=pay&recipients=pranav-r&amount=33.91&note=ShopRite%2010%2F02",
        fallback: "https://venmo.com/pranav-r?txn=pay&amount=33.91&note=ShopRite%2010%2F02",
      },
      { method: "cashapp", label: "Cash App", href: "https://cash.app/$pranavr/33.91" },
      { method: "paypal", label: "PayPal", href: "https://paypal.me/pranavr/33.91" },
    ]);
  });

  it("PAY-05 no handles → no links (the sheet shows the fallback copy) [FR-36]", () => {
    expect(payLinks(NONE, 3391, "x")).toEqual([]);
    expect(payLinks(undefined, 3391, "x")).toEqual([]);
  });

  it("BILL-11 the story has a line for every state change, newest first as served [FR-43]", () => {
    const v = view();
    const at = "2026-10-04T13:12:00Z";
    const entry = (type: string, actor: string | null, payload: Record<string, unknown> | null = null): ActivityEntry => ({
      id: type,
      type,
      actor_name: actor,
      payload,
      created_at: at,
    });
    const meName = "Arjun";
    const text = (e: ActivityEntry) => activityLine(e, { view: v, meName }, "UTC").text;
    expect(text(entry("bill_sent", "Pranav"))).toBe("Pranav sent the tab to the house.");
    expect(text(entry("payment_recorded", "Kabir", { participant: "m:kabir", amount_cents: 1889, status: "pending_confirmation" }))).toBe(
      "Kabir paid $18.89.",
    );
    expect(text(entry("payment_recorded", "Pranav", { participant: "m:kabir", amount_cents: 500, status: "confirmed" }))).toBe(
      "Pranav recorded $5.00 from Kabir.",
    );
    expect(text(entry("payment_confirmed", "Pranav", { participant: "m:arjun", amount_cents: 1000 }))).toBe(
      "Pranav confirmed your $10.00.",
    );
    expect(text(entry("bill_settled", "Pranav"))).toBe("Pranav stamped it settled.");
    expect(text(entry("bill_reopened", "Pranav"))).toBe("Pranav reopened the tab.");
    expect(text(entry("bill_voided", "Pranav", { reason: "Wrong store" }))).toBe("Pranav voided the tab: Wrong store");
    expect(text(entry("reminder_sent", "Pranav", { member_id: "meera" }))).toBe("Pranav nudged Meera.");
    expect(text(entry("allocation_changed", "Meera", { changes: [{ item_id: "milk", participant: "m:meera", on: true }] }))).toBe(
      "Meera hopped on Whole milk.",
    );
    expect(text(entry("allocation_changed", "Pranav", { changes: [{ item_id: "milk", participant: "m:kabir", on: false }] }))).toBe(
      "Pranav moved Kabir off Whole milk.",
    );
    expect(text(entry("allocation_changed", "Arjun", { changes: [{ item_id: "milk", participant: "m:arjun", on: false }] }))).toBe(
      "You hopped off Whole milk.",
    );
    expect(text(entry("something_new", null))).toBe("Someone updated the tab.");
    const line = activityLine(entry("payment_rejected", "Pranav", { participant: "m:meera", amount_cents: 500 }), { view: v, meName }, "UTC");
    expect(line).toMatchObject({ kind: "payment", text: "Pranav rejected Meera's $5.00.", when: "SUN 10/04 · 1:12 PM" });
    expect(activityLine(entry("item_edited", "Pranav", { name: "Eggs" }), { view: v, meName }).kind).toBe("edit");
    expect(activityLine(entry("bill_sent", "Pranav"), { view: v, meName }).kind).toBe("system");
  });

  it("SPLIT-08 the change banner shows a signed delta [FR-35]", () => {
    expect(signedMoney(336)).toBe("+$3.36");
    expect(signedMoney(-169)).toBe("−$1.69");
  });

  it("BILL-10 reopen is offered for 30 days after settling [FR-40]", () => {
    const settled = "2026-10-01T12:00:00Z";
    const day = 24 * 60 * 60 * 1000;
    expect(canReopen(settled, Date.parse(settled) + 30 * day)).toBe(true);
    expect(canReopen(settled, Date.parse(settled) + 30 * day + 1)).toBe(false);
    expect(canReopen(null, Date.now())).toBe(false);
  });

  it("PAY-07 the bell is off for 48 h after a nudge to that member [FR-41]", () => {
    const at = "2026-10-03T10:00:00Z";
    const entries: ActivityEntry[] = [
      { id: "r", type: "reminder_sent", actor_name: "Pranav", payload: { member_id: "arjun" }, created_at: at },
    ];
    expect(remindedRecently(entries, "arjun", Date.parse(at) + 47 * 3600_000)).toBe(true);
    expect(remindedRecently(entries, "arjun", Date.parse(at) + 48 * 3600_000)).toBe(false);
    expect(remindedRecently(entries, "meera", Date.parse(at))).toBe(false);
  });

  it("SYNC-10 a second payment within 2 minutes is described in words", () => {
    const at = "2026-10-03T10:00:00Z";
    expect(agoText(at, Date.parse(at) + 10_000)).toBe("just now");
    expect(agoText(at, Date.parse(at) + 60_000)).toBe("a minute ago");
    expect(agoText(at, Date.parse(at) + 115_000)).toBe("two minutes ago");
  });

  it("SYNC-06 state conflicts get their own copy; everything else shows the server's title [SYNC-03] [SYNC-07]", () => {
    expect(problemText(new ApiError(409, { code: "BILL_SETTLED", title: "This tab is settled" }))).toBe(
      "This tab was just settled.",
    );
    expect(problemText(new ApiError(409, { code: "LAST_PERSON", title: "Every item needs someone on it" }))).toBe(
      "You're the last one on it. Ask the payer to remove the item.",
    );
    expect(
      problemText(new ApiError(409, { code: "ALREADY_ANSWERED", title: "x", payment: { status: "rejected" } })),
    ).toBe("Already answered: rejected.");
    expect(problemText(new ApiError(404, { code: "ITEM_GONE", title: "That item was removed" }))).toBe(
      "That item was removed.",
    );
    expect(problemText(new ApiError(429, { code: "RATE_LIMITED", title: "You already nudged them recently" }))).toBe(
      "You already nudged them recently",
    );
    expect(problemText(new ApiError(0, { code: "NETWORK" }))).toBe("Couldn't save");
  });

  it("FR-39 the open stamp counts days since the tab was sent [FR-39]", () => {
    const sent = "2026-10-02T23:31:00Z";
    expect(dayOpen(sent, Date.parse(sent) + 1000)).toBe(1);
    expect(dayOpen(sent, Date.parse(sent) + 26 * 3600_000)).toBe(2);
    expect(formatWhen("2026-10-02T23:31:00Z", "America/New_York")).toBe("FRI 10/02 · 7:31 PM");
  });
});
