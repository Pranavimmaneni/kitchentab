import { describe, expect, it } from "vitest";
import {
  FILTERS,
  ageLabel,
  askLabel,
  askNote,
  askedLabel,
  canUndo,
  chipInCents,
  coOwnerNote,
  coOwnersLine,
  doneNote,
  helpNote,
  joinNames,
  matchesFilter,
  outgoingText,
  parsePercent,
  partBody,
  requestPrice,
  requestStamp,
  sheetMode,
  showsOpenBadge,
  sourceLine,
  splitDeltaChips,
  tileLabel,
} from "../../lib/kitchen";
import { withMyConsent } from "../../lib/kitchen-writes";
import { strings } from "../../lib/strings";
import { ME, item, owner, request } from "./fixtures/kitchen";

const NOW = new Date("2026-10-03T19:00:00Z");
const NAMES = { "m-pranav": "Pranav", "m-arjun": "Arjun", "m-meera": "Meera", "m-kabir": "Kabir" };

describe("kitchen display logic", () => {
  it("KITCH-07 filters mirror the server keys (OPEN = open for me) and count like the API [FR-66]", () => {
    const items = [
      item({ id: "a", mine: true, open_for_me: true, use_soon: true }),
      item({ id: "b", mine: false, open_for_me: false, owners: [owner("meera")] }),
      item({
        id: "c",
        mine: false,
        open_for_me: true,
        is_open: true,
        owners: [owner("kabir", true)],
      }),
    ];
    const counts = Object.fromEntries(
      FILTERS.map((f) => [f, items.filter((i) => matchesFilter(i, f)).length]),
    );
    expect(counts).toEqual({ all: 3, mine: 1, open: 2, use_soon: 1 });
    // Nothing is removed by a filter: every item is still there to dim.
    expect(items.map((i) => i.id)).toEqual(["a", "b", "c"]);
  });

  it("KITCH-08 the owner's note names who is still holding out, and OPEN only when everyone says yes [FR-69]", () => {
    const milk = item({
      owners: [owner("pranav", true), owner("arjun", false), owner("meera", false)],
    });
    expect(coOwnerNote(milk, ME)).toBe(
      "Arjun & Meera haven’t opened it up, so others still ask first.",
    );
    expect(coOwnerNote(item({ owners: [owner("pranav", true), owner("arjun", false)] }), ME)).toBe(
      "Arjun hasn’t opened it up, so others still ask first.",
    );
    expect(coOwnerNote(item({ owners: [owner("pranav", false), owner("arjun", true)] }), ME)).toBe(
      strings.item.coOff,
    );
    expect(coOwnerNote(item({ owners: [owner("pranav", true), owner("arjun", true)] }), ME)).toBe(
      "Everyone who owns it is happy to share.",
    );
    expect(coOwnerNote(item({ owners: [owner("pranav", true)] }), ME)).toBe(
      "Housemates can help themselves.",
    );
    // Optimistic switch (set-state PUT): is_open follows every owner's consent.
    const mine = item({ owners: [owner("pranav", false), owner("arjun", true)] });
    const flipped = withMyConsent(mine, ME, true);
    expect(flipped.is_open).toBe(true);
    expect(flipped.holdouts).toEqual([]);
    expect(withMyConsent(flipped, ME, false).holdouts).toEqual(["Pranav"]);
    expect(withMyConsent(item({ owners: [owner("arjun")] }), ME, true).is_open).toBe(false);
  });

  it("KITCH-07 tiles say name, owners, share state and use soon to screen readers [FR-67]", () => {
    const spinach = item({
      name: "Baby spinach",
      owners: [owner("pranav", true), owner("meera")],
      use_soon: true,
    });
    expect(tileLabel(spinach, ME)).toBe("Baby spinach, owned by you & Meera, ask first, use soon");
    const rice = item({
      name: "Basmati rice 10 lb",
      owners: [owner("pranav", true), owner("arjun", true)],
      is_open: true,
    });
    expect(tileLabel(rice, ME)).toBe("Basmati rice 10 lb, owned by you & Arjun, open to share");
    expect(showsOpenBadge(item({ is_open: false, granted_to_me: true }))).toBe(true);
    expect(joinNames(["Pranav", "Arjun", "Meera"])).toBe("Pranav, Arjun & Meera");
  });

  it("ASK-02 the sheet shows owner controls to owners, Help yourself when open and Ask when locked [FR-68]", () => {
    expect(sheetMode(item())).toBe("owner");
    const bars = item({ owners: [owner("arjun")], mine: false, open_for_me: false });
    expect(sheetMode(bars)).toBe("locked");
    expect(askNote(bars)).toBe("Arjun hasn’t opened this up. Ask first?");
    expect(askLabel(bars)).toBe("Ask Arjun →");
    expect(sheetMode({ ...bars, my_request: { id: "r", amount: "half" } })).toBe("asked");
    expect(askedLabel(bars)).toBe("ASKED · WAITING ON ARJUN");
    const coffee = item({
      owners: [owner("meera"), owner("kabir")],
      mine: false,
      open_for_me: false,
    });
    expect(askNote(coffee)).toBe("Meera & Kabir own this. Any of them can say yes.");
    expect(askedLabel(coffee)).toBe("ASKED · WAITING ON AN OWNER");
    const ramen = item({
      owners: [owner("arjun", true), owner("meera", true)],
      mine: false,
      is_open: true,
      open_for_me: true,
    });
    expect(sheetMode(ramen)).toBe("open");
    expect(helpNote(ramen)).toBe("Arjun & Meera are happy to share.");
  });

  it("ASK-03 a granted item is open for the asker only and names who approved it [FR-72]", () => {
    const chicken = item({
      owners: [owner("pranav"), owner("arjun")],
      mine: false,
      open_for_me: true,
      granted_to_me: true,
      granted_by: "Pranav",
    });
    expect(sheetMode(chicken)).toBe("open");
    expect(helpNote(chicken)).toBe("Approved by Pranav, just for you.");
    expect(sourceLine(chicken)).toBe("SHOPRITE · OCT 2 · $11.37");
  });

  it("KITCH-13 leftovers have no source tab, so the line says when they were added [FR-79]", () => {
    expect(sourceLine(item({ source: null, price_cents: null, stocked_on: "2026-10-06" }))).toBe(
      "LEFTOVERS · ADDED OCT 6",
    );
  });

  it("ASK-04 Add to split shows the server's exact deltas: KABIR +$3.83 · YOU −$1.92 · ARJUN −$1.91 [FR-72] [FR-73]", () => {
    const req = request();
    expect(splitDeltaChips(req.split_deltas!, NAMES, ME)).toEqual([
      "KABIR +$3.83",
      "YOU −$1.92",
      "ARJUN −$1.91",
    ]);
    expect(coOwnersLine(req.co_owners!)).toBe("You and Arjun own this. Either of you can answer.");
    expect(coOwnersLine([])).toBe("Only you own this.");
    expect(coOwnersLine(["Arjun", "Meera"])).toBe(
      "You, Arjun & Meera own this. Any of you can answer.",
    );
  });

  it("ASK-05 chip in accepts $0.01 up to the line total only [FR-72] [FR-74]", () => {
    expect(chipInCents("3.79", 1137)).toBe(379);
    expect(chipInCents("0.01", 1137)).toBe(1);
    expect(chipInCents("11.37", 1137)).toBe(1137);
    expect(chipInCents("11.38", 1137)).toBeNull();
    expect(chipInCents("0", 1137)).toBeNull();
    expect(chipInCents("-1", 1137)).toBeNull();
    expect(chipInCents("abc", 1137)).toBeNull();
    const done = request({ status: "approved", resolution: "chip_in", chip_in_cents: 379 });
    expect(requestStamp(done, NOW)).toEqual({ text: "YES · CHIP IN $3.79", tone: "lime" });
    expect(doneNote(done, ME)).toBe("Kabir owes $3.79, split between the owners.");
  });

  it("ASK-06 a decline reads Not this time with no reason [FR-72]", () => {
    const no = request({ status: "declined", answered_by: { id: ME, display_name: "Pranav" } });
    expect(requestStamp(no, NOW).text).toBe("NOT THIS TIME");
    expect(doneNote(no, ME)).toBe("Kabir gets a polite no.");
    const byArjun = request({
      status: "approved",
      resolution: "split",
      answered_by: { id: "m-arjun", display_name: "Arjun" },
    });
    expect(doneNote(byArjun, ME)).toBe(
      "Arjun answered. Kabir’s share on the ShopRite tab went up. Everyone was notified.",
    );
  });

  it("ASK-09 undo shows only inside the server's 5-minute window [FR-76]", () => {
    const answered = request({
      status: "approved",
      resolution: "free",
      undo_until: "2026-10-03T19:04:00Z",
    });
    expect(canUndo(answered, NOW)).toBe(true);
    expect(canUndo(answered, new Date("2026-10-03T19:05:00Z"))).toBe(false);
    expect(canUndo(request(), NOW)).toBe(false);
    expect(canUndo(request({ status: "expired", undo_until: "2026-10-03T19:04:00Z" }), NOW)).toBe(
      false,
    );
  });

  it("ASK-13 request cards age and stamp like Requests.dc.html [FR-75]", () => {
    expect(ageLabel("2026-10-03T18:48:00Z", NOW)).toBe("12 MIN AGO");
    expect(ageLabel("2026-10-03T18:00:00Z", NOW)).toBe("1 HOUR AGO");
    expect(ageLabel("2026-10-01T18:00:00Z", NOW)).toBe("2 DAYS AGO");
    expect(ageLabel("2026-10-03T18:59:40Z", NOW)).toBe("JUST NOW");
    expect(requestStamp(request({ created_at: "2026-10-03T18:55:00Z" }), NOW)).toEqual({
      text: "WAITING · 5 MIN",
      tone: "paper",
    });
    expect(requestStamp(request({ status: "approved", resolution: "free" }), NOW).text).toBe(
      "YES · ON THE HOUSE",
    );
    expect(requestStamp(request({ status: "approved", resolution: "split" }), NOW).text).toBe(
      "YES · ADDED TO SPLIT",
    );
    expect(
      outgoingText(
        request({
          owners: ["Arjun"],
          amount_words: "half of",
          item: { ...request().item, short_name: "Protein bars" },
        }),
      ),
    ).toBe("You asked Arjun for half of the protein bars.");
  });
});

describe("custom split on asks (FR-81, FR-82)", () => {
  it("ASK-15 quick choices send a percent; Equal share sends nothing extra [FR-81]", () => {
    expect(partBody("split", "base", "dollars", "", 1137, null)).toEqual({});
    expect(partBody("split", 25, "dollars", "", 1137, null)).toEqual({ percent: 25 });
    expect(partBody("split", 100, "dollars", "", 1137, null)).toEqual({ percent: 100 });
    expect(partBody("chip_in", 50, "dollars", "", 1137, 379)).toEqual({ percent: 50 });
    expect(partBody("chip_in", "base", "dollars", "", 1137, 379)).toEqual({ chip_in_cents: 379 });
  });

  it("ASK-16 Custom takes a whole percent or the owner's own value up to the price [FR-81]", () => {
    expect(partBody("split", "custom", "percent", "33", 1137, null)).toEqual({ percent: 33 });
    expect(partBody("split", "custom", "percent", "33%", 1137, null)).toEqual({ percent: 33 });
    for (const bad of ["0", "101", "33.5", "", "abc"])
      expect(partBody("split", "custom", "percent", bad, 1137, null)).toBeNull();
    expect(partBody("split", "custom", "dollars", "5", 1137, null)).toEqual({ amount_cents: 500 });
    expect(partBody("chip_in", "custom", "dollars", "$5.00", 1137, 379)).toEqual({
      chip_in_cents: 500,
    });
    expect(partBody("split", "custom", "dollars", "11.38", 1137, null)).toBeNull();
    expect(partBody("split", "custom", "dollars", "0", 1137, null)).toBeNull();
    expect(parsePercent(" 100 ")).toBe(100);
    expect(parsePercent("1000")).toBeNull();
  });

  it("ASK-18 request cards show the line total, or NO PRICE for leftovers [FR-82]", () => {
    expect(requestPrice({ line_total_cents: 1137 })).toBe("$11.37");
    expect(requestPrice({ line_total_cents: null })).toBe(strings.requests.noPrice);
    expect(requestPrice({})).toBe(strings.requests.noPrice);
  });
});
