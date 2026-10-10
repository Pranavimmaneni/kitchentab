import { describe, expect, it } from "vitest";
import type { BalancePair, TabCard } from "../../lib/api/tabs-types";
import { pickHouse } from "../../lib/house";
import {
  balanceHeadline,
  cardMeta,
  changeNote,
  firstName,
  monDay,
  pairLines,
  relativeDay,
  signedMoney,
  stampDate,
  tabStamp,
} from "../../lib/tabs";

const ME = "m-pranav";

function pair(name: string, net: number): BalancePair {
  return {
    member_id: `m-${name.toLowerCase()}`,
    display_name: name,
    initial: name[0]!,
    color: "#6A4CFF",
    net_cents: net,
    carried_cents: 0,
    bills: [],
  };
}

function card(over: Partial<TabCard> = {}): TabCard {
  return {
    id: "b-1",
    store_name: "ShopRite",
    purchase_date: "2026-10-02",
    state: "open",
    payer_member_id: ME,
    payer_name: "Pranav",
    total_cents: 9518,
    item_count: 12,
    version: 3,
    settled_at: null,
    paid_count: 1,
    debtor_count: 3,
    my_owed_cents: 0,
    changed: null,
    ...over,
  };
}

describe("tabs and balances display", () => {
  it("BAL-01 apt4b reads +$40.37 coming to you with pair rows netted per pair [FR-50] [FR-51]", () => {
    expect(balanceHeadline(4037)).toEqual({ kind: "owed", text: "+$40.37" });
    const lines = pairLines([pair("Arjun", 3391), pair("Meera", 814), pair("Kabir", -168)]);
    expect(lines.map((l) => [l.from, l.to, l.amountCents, l.kind])).toEqual([
      ["Arjun", "YOU", 3391, "owed"],
      ["Meera", "YOU", 814, "owed"],
      ["YOU", "Kabir", 168, "owing"],
    ]);
    expect(balanceHeadline(-1410)).toEqual({ kind: "owing", text: "−$14.10" });
    expect(signedMoney(-5)).toBe("−$0.05");
  });

  it("BAL-04 ALL SQUARE when the net is zero, and square pairs drop out of the ticker [FR-50]", () => {
    expect(balanceHeadline(0)).toEqual({ kind: "square", text: "ALL SQUARE" });
    expect(pairLines([pair("Arjun", 0)])).toEqual([]);
  });

  it("COPY-01 tab cards read {Mon D} · {payer} paid · {n} items with payment progress stamps [FR-52]", () => {
    expect(cardMeta(card(), ME)).toBe("Oct 2 · you paid · 12 items");
    expect(
      cardMeta(card({ payer_member_id: "m-meera", payer_name: "Meera", item_count: 1 }), ME),
    ).toBe("Oct 2 · Meera paid · 1 item");
    expect(tabStamp(card(), ME)).toEqual({ text: "1/3 PAID", tone: "lime" });
    expect(
      tabStamp(
        card({ payer_member_id: "m-meera", my_owed_cents: 1410, purchase_date: "2026-09-27" }),
        ME,
      ),
    ).toEqual({ text: "YOU OWE $14.10", tone: "warn" });
    expect(tabStamp(card({ debtor_count: 0, paid_count: 0 }), ME)?.text).toBe("ALL PAID");
    expect(tabStamp(card({ state: "settled" }), ME)?.text).toBe("SETTLED");
    expect(tabStamp(card({ state: "voided" }), ME)?.text).toBe("VOIDED");
    expect(monDay("2026-09-27")).toBe("Sep 27");
  });

  it("COPY-02 the change note names who tweaked the tab and when; dates per locale [FR-35]", () => {
    const now = new Date(2026, 9, 6, 12);
    const changed = card({
      changed: {
        delta_cents: -191,
        since: "2026-10-03T00:00:00Z",
        by_name: "Meera",
        at: new Date(2026, 9, 4, 18).toISOString(),
      },
    });
    expect(changeNote(changed, now)).toBe("Meera tweaked it Sunday");
    expect(changeNote(card(), now)).toBeNull();
    expect(
      changeNote(card({ changed: { delta_cents: 5, since: "x", by_name: null, at: null } }), now),
    ).toBe("Your share changed");
    expect(relativeDay(new Date(2026, 9, 6, 1), now)).toBe("today");
    expect(relativeDay(new Date(2026, 9, 5, 23), now)).toBe("yesterday");
    expect(relativeDay(new Date(2026, 8, 20), now)).toBe("Sep 20");
    expect(stampDate(new Date(2026, 9, 2))).toBe("OCT 02 2026");
    expect(firstName("Pranav Ram")).toBe("Pranav");
  });

  it("HOUSE-09 the last-used house opens by default, else the newest one [FR-08]", () => {
    const houses = [
      { id: "h2", name: "Beach house", member_id: "m2", role: "member" as const },
      { id: "h1", name: "Apt 4B", member_id: "m1", role: "admin" as const },
    ];
    expect(pickHouse(houses, "h1")?.name).toBe("Apt 4B");
    expect(pickHouse(houses, null)?.name).toBe("Beach house");
    expect(pickHouse(houses, "gone")?.name).toBe("Beach house");
    expect(pickHouse([], "h1")).toBeNull();
  });
});
