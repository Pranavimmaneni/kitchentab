import { expect, test, type Page } from "@playwright/test";
import { call } from "./fixtures/app";
import { addLeftovers, aptWith, closeApt, open, seedShopRite, tile } from "./fixtures/household";

/**
 * Milestone 8 journeys against the real API (PRD §19, TEST_PLAN 3.6): Kitchen panels, the fridge
 * and pantry views, the item sheet, leftovers, asks and answers. Phones only (WebKit, Chromium).
 */
test.skip(({ browserName }) => browserName === "firefox", "phone journeys: WebKit and Chromium");

async function fridgeIds(page: Page, houseId: string): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const place of ["fridge", "pantry"]) {
    const view = await call<{ areas: { items: { id: string; name: string }[] }[] }>(
      page,
      "GET",
      `/houses/${houseId}/inventory?place=${place}`,
    );
    for (const item of view.areas.flatMap((a) => a.items)) out[item.name] = item.id;
  }
  return out;
}

test("KITCH-05 KITCH-06 the Kitchen shows counts and only USE SOON items; Fridge has Freezer + Refrigerated, Pantry one area [FR-63] [FR-64] [FR-65]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(120_000);
  const apt = await aptWith(browser, baseURL, page);
  await seedShopRite(page, apt, { daysAgo: 4 });
  await addLeftovers(page, apt, { name: "Frozen peas", place: "freezer", icon_key: "frozen_bag" });

  await open(page, "/");
  await expect(page.getByRole("heading", { level: 1, name: "The kitchen" })).toBeVisible();
  await expect(page.getByText("KITCHENTAB ** APT 4B")).toBeVisible();
  const fridge = page.getByRole("link", { name: "Open the fridge: 7 items, 2 to use soon" });
  const pantry = page.getByRole("link", { name: "Open the pantry: 4 items, 0 to use soon" });
  await expect(fridge).toBeVisible();
  await expect(pantry).toBeVisible();
  // Only use-soon items are listed (spinach at 80%, chicken past 100%), with their time left.
  await expect(fridge.getByText("USE SOON · 2")).toBeVisible();
  await expect(fridge.getByText("Baby spinach")).toBeVisible();
  await expect(fridge.getByText("1 DAY LEFT")).toBeVisible();
  await expect(fridge.getByText("Chicken thighs")).toBeVisible();
  await expect(fridge.getByText("Whole milk")).toHaveCount(0);
  await expect(pantry.getByText("NOTHING TO USE SOON")).toBeVisible();
  // No filters on the Kitchen screen (FR-64).
  await expect(page.getByRole("toolbar")).toHaveCount(0);

  await fridge.click();
  await page.waitForURL("**/kitchen/fridge");
  await expect(page.getByRole("heading", { level: 1, name: "The fridge" })).toBeVisible();
  await expect(page.getByRole("group", { name: /^freezer, 1 item$/ })).toBeVisible();
  await expect(page.getByRole("group", { name: /^refrigerated, 6 items$/ })).toBeVisible();
  await expect(page.getByRole("group")).toHaveCount(2);
  await expect(tile(page, "Frozen peas")).toBeVisible();

  await open(page, "/kitchen/pantry");
  await expect(page.getByRole("heading", { level: 1, name: "The pantry" })).toBeVisible();
  await expect(page.getByRole("group")).toHaveCount(1);
  await expect(page.getByText("FREEZER")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "+ Add something" })).toBeVisible();
  await closeApt(apt);
});

test("KITCH-07 filters ALL / MINE / OPEN / USE SOON show live counts and dim non-matching tiles to 22% without moving them [FR-66] [FR-67]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(120_000);
  const apt = await aptWith(browser, baseURL, page);
  await seedShopRite(page, apt, { daysAgo: 4 });
  await open(page, "/kitchen/fridge");
  // Pranav owns milk, eggs, chicken and spinach of the 6 fridge lines; the yogurt and oat milk
  // belong to placeholders only, which leaves them open to everyone (PRD §19.9).
  for (const label of ["ALL 6", "MINE 4", "OPEN 6", "USE SOON 2"]) {
    await expect(page.getByRole("button", { name: label })).toBeVisible();
  }
  const yogurt = tile(page, "Greek yogurt 32 oz");
  const before = await yogurt.boundingBox();
  await page.getByRole("button", { name: "MINE 4" }).click();
  await expect(page.getByRole("button", { name: "MINE 4" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(yogurt).toHaveCSS("opacity", "0.22");
  await expect(tile(page, "Whole milk 1 gal")).toHaveCSS("opacity", "1");
  expect(await yogurt.boundingBox()).toEqual(before);

  await page.getByRole("button", { name: "USE SOON 2" }).click();
  await expect(tile(page, "Whole milk 1 gal")).toHaveCSS("opacity", "0.22");
  await expect(tile(page, "Baby spinach")).toHaveCSS("opacity", "1");
  await expect(tile(page, "Baby spinach")).toHaveAccessibleName(
    "Baby spinach, owned by you, ask first, use soon",
  );
  // The choice is remembered on this device.
  await page.reload();
  await expect(page.getByRole("button", { name: "USE SOON 2" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await closeApt(apt);
});

test("KITCH-08 an item is OPEN only when every owner says yes, and the sheet names who is holding out [FR-68] [FR-69]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun", "kabir"]);
  const { arjun, kabir } = apt.pages as Required<typeof apt.pages>;
  await seedShopRite(page, apt);

  await open(page, "/kitchen/fridge");
  await tile(page, "Chicken thighs 3 lb").click();
  const sheet = page.getByRole("dialog", { name: "Chicken thighs 3 lb" });
  await expect(sheet.getByText("Off. Housemates have to ask first.")).toBeVisible();
  await sheet.getByRole("switch", { name: "I'm open to sharing" }).click();
  await expect(sheet.getByRole("switch", { name: "I'm open to sharing" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await expect(
    sheet.getByText("Arjun hasn’t opened it up, so others still ask first."),
  ).toBeVisible();
  await page.keyboard.press("Escape");

  // Kabir still has to ask.
  await open(kabir, "/kitchen/fridge");
  await expect(tile(kabir, "Chicken thighs 3 lb")).toHaveAccessibleName(/ask first/);

  await open(arjun, "/kitchen/fridge");
  await tile(arjun, "Chicken thighs 3 lb").click();
  await arjun.getByRole("switch", { name: "I'm open to sharing" }).click();
  await expect(arjun.getByText("Everyone who owns it is happy to share.")).toBeVisible();

  await expect(async () => {
    await kabir.reload();
    await expect(tile(kabir, "Chicken thighs 3 lb")).toHaveAccessibleName(/open to share/, {
      timeout: 1000,
    });
  }).toPass({ timeout: 15_000 });
  await tile(kabir, "Chicken thighs 3 lb").click();
  const open_ = kabir.getByRole("dialog", { name: "Chicken thighs 3 lb" });
  await expect(open_.getByText("HELP YOURSELF")).toBeVisible();
  await expect(open_.getByText("Pranav & Arjun are happy to share.")).toBeVisible();
  await expect(open_.getByRole("button", { name: /^Ask / })).toHaveCount(0);
  await closeApt(apt);
});

test("KITCH-12 Used up leaves the fridge with a 5 s Undo that brings it back; non-owners get no Used up [FR-77]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  await seedShopRite(page, apt);

  await open(page, "/kitchen/fridge");
  await tile(page, "Baby spinach").click();
  await page
    .getByRole("dialog", { name: "Baby spinach" })
    .getByRole("button", { name: "Used up" })
    .click();
  await expect(page.getByRole("dialog", { name: "Baby spinach" })).toBeHidden();
  await expect(tile(page, "Baby spinach")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "ALL 5" })).toBeVisible();
  await expect(page.getByRole("status").getByText("Baby spinach marked used up")).toBeVisible();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(tile(page, "Baby spinach")).toBeVisible();
  await expect(page.getByRole("button", { name: "ALL 6" })).toBeVisible();

  // Kabir doesn't own the chicken: no owner controls, only the ask.
  await open(kabir, "/kitchen/fridge");
  await tile(kabir, "Chicken thighs 3 lb").click();
  const sheet = kabir.getByRole("dialog", { name: "Chicken thighs 3 lb" });
  await expect(sheet.getByRole("button", { name: "Ask Pranav →" })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Used up" })).toHaveCount(0);
  await closeApt(apt);
});

test("KITCH-13 EDGE-19-07 ASK-11 leftovers have no bill and no price, and asks on them offer only On the house [FR-79] [FR-72]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;

  await open(page, "/kitchen/fridge");
  await page.getByRole("button", { name: "+ Add leftovers" }).click();
  const sheet = page.getByRole("dialog", { name: "Add leftovers" });
  await sheet.getByLabel("WHAT IS IT?").fill("Dal from Sunday");
  await sheet.getByRole("radio", { name: "Leftovers box" }).click();
  await sheet.getByRole("button", { name: "Add it" }).click();
  await expect(sheet).toBeHidden();
  await tile(page, "Dal from Sunday").click();
  const item = page.getByRole("dialog", { name: "Dal from Sunday" });
  await expect(item.getByText(/^LEFTOVERS · ADDED /)).toBeVisible();
  await expect(item.getByText("$")).toHaveCount(0);
  await page.keyboard.press("Escape");

  await open(kabir, "/kitchen/fridge");
  await tile(kabir, "Dal from Sunday").click();
  await kabir.getByRole("button", { name: "Ask Pranav →" }).click();
  await expect(kabir.getByText("ASKED · WAITING ON PRANAV")).toBeVisible();

  await open(page, "/kitchen/requests");
  const card = page.getByRole("article", { name: "Kabir wants a bit of your dal" });
  await expect(card.getByRole("radio", { name: /On the house/ })).toBeVisible();
  await expect(card.getByRole("radio")).toHaveCount(1);
  await closeApt(apt);
});

test("KITCH-16 EDGE-19-10 a fridge area with 60+ items virtualizes and every item stays reachable by scrolling the page [FR-65]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(180_000);
  // Two roommates stock it so neither hits the 60 writes a minute limit.
  const apt = await aptWith(browser, baseURL, page, ["kabir"]);
  const kabir = apt.pages.kabir!;
  for (let i = 1; i <= 64; i += 2) {
    await Promise.all([
      addLeftovers(page, apt, { name: `Leftover ${String(i).padStart(2, "0")}`, place: "fridge" }),
      addLeftovers(kabir, apt, {
        name: `Leftover ${String(i + 1).padStart(2, "0")}`,
        place: "fridge",
      }),
    ]);
  }
  await open(page, "/kitchen/fridge");
  await expect(page.getByRole("button", { name: "ALL 64" })).toBeVisible();
  const area = page.getByRole("group", { name: "refrigerated, 64 items" });
  await expect(area).toHaveAttribute("data-virtual", "true");
  // Only the rows near the viewport are in the DOM at first.
  expect(await area.getByRole("button").count()).toBeLessThan(64);
  // No inner scroll panel: the page itself scrolls (VIS-03).
  const inner = await page.evaluate(
    () =>
      [...document.querySelectorAll("main *")].filter((el) => {
        const s = getComputedStyle(el);
        return /(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1;
      }).length,
  );
  expect(inner).toBe(0);
  // Scroll the page to the bottom: the last rows render.
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect(async () => {
    const names = await area
      .getByRole("button")
      .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
    const numbers = names.map((n) => Number(/Leftover (\d\d)/.exec(n ?? "")?.[1]));
    // Items are newest first (same category), so the oldest leftover sits at the bottom.
    expect(numbers).toContain(1);
  }).toPass({ timeout: 5000 });
  await tile(page, "Leftover 01").click();
  await expect(page.getByRole("dialog", { name: "Leftover 01" })).toBeVisible();
  await closeApt(apt);
});

test("ASK-01 ASK-13 a non-owner asks with an amount and a 140-character note; owners see it in FOR YOU with counts and a Kitchen badge [FR-71] [FR-75]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun", "kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  await seedShopRite(page, apt);

  await open(kabir, "/kitchen/fridge");
  await tile(kabir, "Chicken thighs 3 lb").click();
  const sheet = kabir.getByRole("dialog", { name: "Chicken thighs 3 lb" });
  await expect(sheet.getByText("Pranav & Arjun own this. Any of them can say yes.")).toBeVisible();
  await sheet.getByRole("radio", { name: "HALF" }).click();
  await expect(sheet.getByRole("radio", { name: "HALF" })).toHaveAttribute("aria-checked", "true");
  await sheet.getByLabel("NOTE (OPTIONAL)").fill("x".repeat(150));
  await expect(sheet.getByLabel("NOTE (OPTIONAL)")).toHaveValue("x".repeat(140));
  await expect(sheet.getByText("140/140")).toBeVisible();
  await sheet.getByLabel("NOTE (OPTIONAL)").fill("For tonight’s curry");
  await sheet.getByRole("button", { name: "Ask Pranav & Arjun →" }).click();
  await expect(sheet.getByText("ASKED · WAITING ON AN OWNER")).toBeVisible();
  await expect(kabir.getByRole("status").getByText("Request sent to Pranav & Arjun")).toBeVisible();
  await kabir.keyboard.press("Escape");
  await tile(kabir, "Baby spinach").click();
  await kabir.getByRole("button", { name: "Ask Pranav →" }).click();
  await expect(kabir.getByText("ASKED · WAITING ON PRANAV")).toBeVisible();

  await open(page, "/");
  await expect(page.getByTestId("asks-pill")).toHaveText("2 ASKS FOR YOU →");
  await page.getByTestId("asks-pill").click();
  await page.waitForURL("**/kitchen/requests");
  await expect(page.getByRole("tab", { name: "FOR YOU · 2" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const card = page.getByRole("article", { name: "Kabir wants half of your chicken thighs" });
  await expect(card.getByText("For tonight’s curry")).toBeVisible();
  await expect(card.getByText("You and Arjun own this. Either of you can answer.")).toBeVisible();

  await open(kabir, "/kitchen/requests");
  await kabir.getByRole("tab", { name: "YOU ASKED · 2" }).click();
  await expect(
    kabir.getByText("You asked Pranav & Arjun for half of the chicken thighs."),
  ).toBeVisible();
  await closeApt(apt);
});

test("ASK-03 ASK-09 On the house grants the asker access (approved by the owner); the owner can undo within 5 minutes [FR-72] [FR-76]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun", "kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  await seedShopRite(page, apt);
  const ids = await fridgeIds(page, apt.id);
  await call(kabir, "POST", `/inventory/${ids["Chicken thighs 3 lb"]}/requests`, {
    amount: "a_bit",
  });

  await open(page, "/kitchen/requests");
  const card = page.getByRole("article", { name: "Kabir wants a bit of your chicken thighs" });
  await expect(card.getByRole("radio", { name: /On the house/ })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await card.getByRole("button", { name: "Say yes" }).click();
  await expect(card.getByText("YES · ON THE HOUSE")).toBeVisible();
  await expect(card.getByText("Kabir can help themselves.")).toBeVisible();

  // Undo puts the ask back to pending, then say yes again.
  await card.getByRole("button", { name: "UNDO" }).click();
  await expect(card.getByText("HOW SHOULD IT WORK?")).toBeVisible();
  await card.getByRole("button", { name: "Say yes" }).click();
  await expect(card.getByText("YES · ON THE HOUSE")).toBeVisible();

  await open(kabir, "/kitchen/fridge");
  await expect(tile(kabir, "Chicken thighs 3 lb")).toHaveAccessibleName(/open to share/);
  await tile(kabir, "Chicken thighs 3 lb").click();
  const sheet = kabir.getByRole("dialog", { name: "Chicken thighs 3 lb" });
  await expect(sheet.getByText("HELP YOURSELF")).toBeVisible();
  await expect(sheet.getByText("Approved by Pranav, just for you.")).toBeVisible();
  // Only for Kabir: the item itself is still locked.
  await open(page, "/kitchen/fridge");
  await expect(tile(page, "Chicken thighs 3 lb")).toHaveAccessibleName(/ask first/);
  await closeApt(apt);
});

test("ASK-04 MONEY-09 Add to split shows KABIR +$3.83 · YOU −$1.92 · ARJUN −$1.91 before saying yes, and Kabir joins the line [FR-72] [FR-73]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun", "kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  // shoprite_after_meera_joins_coffee → request_add_to_split (packages/fixtures/money_cases.json).
  const { billId } = await seedShopRite(page, apt, { meeraOnCoffee: true });
  const before = await call<{ shares: Record<string, number> }>(page, "GET", `/bills/${billId}`);
  const ids = await fridgeIds(page, apt.id);
  await call(kabir, "POST", `/inventory/${ids["Chicken thighs 3 lb"]}/requests`, {
    amount: "a_bit",
  });

  await open(page, "/kitchen/requests");
  const card = page.getByRole("article", { name: "Kabir wants a bit of your chicken thighs" });
  await card.getByRole("radio", { name: /Add them to the split/ }).click();
  await expect(card.getByText("Kabir joins this item on the open ShopRite tab.")).toBeVisible();
  await expect(card.getByTestId("split-deltas")).toHaveText("KABIR +$3.83YOU −$1.92ARJUN −$1.91");
  await card.getByRole("button", { name: "Say yes" }).click();
  await expect(card.getByText("YES · ADDED TO SPLIT")).toBeVisible();
  await expect(
    card.getByText("Kabir’s share on the ShopRite tab went up. Everyone was notified."),
  ).toBeVisible();

  // The server's shares moved by exactly the deltas shown.
  const after = await call<{ shares: Record<string, number> }>(page, "GET", `/bills/${billId}`);
  const delta = (who: "pranav" | "arjun" | "kabir") =>
    after.shares[`m:${apt.member[who]}`]! - before.shares[`m:${apt.member[who]}`]!;
  expect([delta("kabir"), delta("pranav"), delta("arjun")]).toEqual([383, -192, -191]);
  // Kabir now owns the chicken.
  await open(kabir, "/kitchen/fridge");
  await tile(kabir, "Chicken thighs 3 lb").click();
  await expect(kabir.getByRole("switch", { name: "I'm open to sharing" })).toBeVisible();
  await closeApt(apt);
});

test("ASK-05 EDGE-19-04 once the tab is settled, Chip in is prefilled with the would-be share and editable from $0.01 to the line total [FR-72] [FR-74]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun", "kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  const { billId } = await seedShopRite(page, apt);
  const ids = await fridgeIds(page, apt.id);
  await call(kabir, "POST", `/inventory/${ids["Chicken thighs 3 lb"]}/requests`, {
    amount: "half",
  });
  await call(page, "POST", `/bills/${billId}/settle`, { confirm_outstanding: true });

  await open(page, "/kitchen/requests");
  const card = page.getByRole("article", { name: "Kabir wants half of your chicken thighs" });
  await expect(card.getByRole("radio", { name: /Add them to the split/ })).toHaveCount(0);
  await card.getByRole("radio", { name: /Ask them to chip in/ }).click();
  const amount = card.getByLabel("CHIP IN");
  await expect(amount).toHaveValue("3.79");
  await amount.fill("11.38");
  await expect(card.getByText("Between $0.01 and $11.37.")).toBeVisible();
  await expect(card.getByRole("button", { name: "Say yes" })).toBeDisabled();
  await amount.fill("0");
  await expect(card.getByRole("button", { name: "Say yes" })).toBeDisabled();
  await amount.fill("3.79");
  await card.getByRole("button", { name: "Say yes" }).click();
  await expect(card.getByText("YES · CHIP IN $3.79")).toBeVisible();

  // The chip-in shows up in Kabir's balances, split between the two owners (Pranav's side also
  // carries Kabir's $18.89 from settling with money outstanding).
  const balances = await call<{ pairs: { display_name: string; carried_cents: number }[] }>(
    kabir,
    "GET",
    `/houses/${apt.id}/balances`,
  );
  const carried = Object.fromEntries(balances.pairs.map((p) => [p.display_name, p.carried_cents]));
  expect([-190, -189]).toContain(carried.Arjun);
  expect(carried.Pranav! + carried.Arjun!).toBe(-1889 - 379);
  await closeApt(apt);
});

test("ASK-06 ASK-08 Not this time shows a stamp and no reason; the asker can cancel a pending ask [FR-72] [FR-76]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  await seedShopRite(page, apt);
  const ids = await fridgeIds(page, apt.id);
  await call(kabir, "POST", `/inventory/${ids["Chicken thighs 3 lb"]}/requests`, {
    amount: "a_bit",
    note: "Curry?",
  });
  await call(kabir, "POST", `/inventory/${ids["Baby spinach"]}/requests`, { amount: "all" });

  await open(page, "/kitchen/requests");
  const card = page.getByRole("article", { name: "Kabir wants a bit of your chicken thighs" });
  await card.getByRole("button", { name: "Not this time" }).click();
  await expect(card.getByText("NOT THIS TIME")).toBeVisible();
  await expect(card.getByText("Kabir gets a polite no.")).toBeVisible();

  await open(kabir, "/kitchen/requests");
  await kabir.getByRole("tab", { name: /YOU ASKED/ }).click();
  await expect(kabir.getByRole("tab", { name: "YOU ASKED · 1" })).toBeVisible();
  const chicken = kabir.getByRole("article").filter({ hasText: "chicken thighs" });
  await expect(chicken.getByText("NOT THIS TIME")).toBeVisible();
  await expect(chicken.getByText(/because|reason/i)).toHaveCount(0);
  const spinach = kabir.getByRole("article").filter({ hasText: "baby spinach" });
  await spinach.getByRole("button", { name: "CANCEL" }).click();
  await expect(spinach.getByText("CANCELLED")).toBeVisible();
  await expect(kabir.getByRole("tab", { name: "YOU ASKED · 0" })).toBeVisible();
  await closeApt(apt);
});
