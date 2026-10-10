import { fileURLToPath } from "node:url";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { call } from "./fixtures/app";
import { aptWith, closeApt, open, seedHMart, seedShopRite, tile } from "./fixtures/household";

/**
 * Review screenshots of the Milestone 7–8 screens at 390 px in light and dark (Chromium only:
 * matched by the `screenshots.spec.ts` ignore rule for the other projects), written to
 * apps/web/test-results/screens/{screen}-{theme}.png beside the M3–M4 ones. Every shot also runs
 * axe (WCAG 2.2 AA): no serious or critical violations in either theme.
 */
const OUT = fileURLToPath(new URL("../../test-results/screens/", import.meta.url));

type Blocking = { screen: string; theme: string; id: string; targets: string[] };

async function shoot(page: Page, name: string, found: Blocking[], fullPage = true): Promise<void> {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.evaluate(() => document.fonts.ready);
    if (fullPage) {
      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      await page.setViewportSize({ width: 390, height: Math.max(844, height) });
    }
    await page.screenshot({ path: `${OUT}${name}-${theme}.png` });
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    for (const v of results.violations) {
      if (v.impact === "serious" || v.impact === "critical") {
        found.push({
          screen: name,
          theme,
          id: v.id,
          targets: v.nodes.slice(0, 4).map((n) => n.target.join(" ")),
        });
      }
    }
    await page.setViewportSize({ width: 390, height: 844 });
  }
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
}

test("VIS-01 A11Y-01 screenshots of K1–K4, Tabs, Balances, House and Settings at 390 px in both themes, axe clean [FR-63] [FR-75]", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(240_000);
  const options = {
    baseURL,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    reducedMotion: "reduce" as const,
  };
  const context = await browser.newContext(options);
  const page = await context.newPage();
  const apt = await aptWith(browser, baseURL, page, ["arjun", "meera", "kabir"]);
  const { arjun, meera, kabir } = apt.pages as Required<typeof apt.pages>;
  const found: Blocking[] = [];

  // apt4b: ShopRite 4 days ago (Meera on the coffee), Kabir paid $18.89, H Mart paid by Meera.
  const shoprite = await seedShopRite(page, apt, { daysAgo: 4, meeraOnCoffee: true });
  await call(page, "POST", `/bills/${shoprite.billId}/payments`, {
    amount_cents: 1889,
    method: "cash",
    participant: `m:${apt.member.kabir}`,
  });
  await seedHMart(page, apt);
  const inv = await call<{ areas: { items: { id: string; name: string }[] }[] }>(
    page,
    "GET",
    `/houses/${apt.id}/inventory?place=fridge`,
  );
  const pantry = await call<{ areas: { items: { id: string; name: string }[] }[] }>(
    page,
    "GET",
    `/houses/${apt.id}/inventory?place=pantry`,
  );
  const id = (name: string) =>
    [...inv.areas, ...pantry.areas].flatMap((a) => a.items).find((i) => i.name.startsWith(name))!
      .id;
  const share = (who: Page, names: string[]) =>
    Promise.all(names.map((n) => call(who, "PUT", `/inventory/${id(n)}/sharing`, { open: true })));
  await share(page, [
    "Whole milk",
    "Eggs",
    "Basmati rice",
    "Chicken thighs",
    "Baby spinach",
    "Bananas",
    "Shin ramyun",
  ]);
  await share(arjun, ["Whole milk", "Eggs", "Basmati rice", "Shin ramyun"]);
  await share(meera, ["Whole milk", "Eggs", "Basmati rice", "Kimchi", "Shin ramyun"]);
  await share(kabir, ["Eggs", "Basmati rice", "Oat milk", "Kimchi", "Shin ramyun"]);
  await call(kabir, "POST", `/inventory/${id("Chicken thighs")}/requests`, {
    amount: "a_bit",
    note: "For tonight’s curry",
  });
  await call(meera, "POST", `/inventory/${id("Bananas")}/requests`, {
    amount: "all",
    note: "Banana bread for the house",
  });

  // K1 · Kitchen
  await open(page, "/");
  await expect(
    page.getByRole("link", { name: /^Open the fridge: 10 items, 3 to use soon/ }),
  ).toBeVisible();
  await expect(page.getByTestId("asks-pill")).toContainText("2 ASKS FOR YOU");
  await shoot(page, "K1-kitchen", found, false);

  // K2 · Fridge, then the owner sheet for the chicken
  await open(page, "/kitchen/fridge");
  await expect(page.getByRole("button", { name: "ALL 10" })).toBeVisible();
  await shoot(page, "K2-fridge", found);
  await tile(page, "Chicken thighs 3 lb").click();
  await expect(page.getByRole("switch", { name: "I'm open to sharing" })).toBeVisible();
  await shoot(page, "K2-item-sheet-owner", found, false);
  await page.keyboard.press("Escape");

  // K3 · Pantry with the ask sheet for Arjun's protein bars (Pantry.dc.html opens with it)
  await open(page, "/kitchen/pantry");
  await tile(page, "Protein bars 12 ct").click();
  const sheet = page.getByRole("dialog", { name: "Protein bars 12 ct" });
  await sheet.getByRole("radio", { name: "HALF" }).click();
  await expect(sheet.getByRole("button", { name: "Ask Arjun →" })).toBeVisible();
  await shoot(page, "K3-pantry-ask", found, false);
  await sheet.getByRole("button", { name: "Ask Arjun →" }).click();
  await expect(sheet.getByText("ASKED · WAITING ON ARJUN")).toBeVisible();
  await page.keyboard.press("Escape");

  // K4 · Requests: FOR YOU and YOU ASKED
  await open(page, "/kitchen/requests");
  await expect(page.getByRole("tab", { name: "FOR YOU · 2" })).toBeVisible();
  await page
    .getByRole("article", { name: /Meera wants all of your bananas/ })
    .getByRole("radio", { name: /Add them to the split/ })
    .click();
  await shoot(page, "K4-requests-for-you", found);
  await page.getByRole("tab", { name: "YOU ASKED · 1" }).click();
  await expect(page.getByText("You asked Arjun for half of the protein bars.")).toBeVisible();
  await shoot(page, "K4-requests-you-asked", found);

  // 1 · Tabs, Balances, House, Settings
  await open(page, "/tabs");
  await expect(page.getByText("+$40.37")).toBeVisible();
  await shoot(page, "1-tabs", found);
  await open(page, "/balances");
  await expect(page.getByText("Arjun owes you")).toBeVisible();
  await page.getByRole("button", { name: /Meera owes you/ }).click();
  await shoot(page, "balances", found);
  await open(page, "/house");
  await expect(page.getByTestId("invite-link")).toContainText("/h/");
  await expect(page.getByRole("heading", { name: "KITCHEN HISTORY · LAST 30 DAYS" })).toBeVisible();
  await shoot(page, "house", found);
  await open(page, "/settings");
  await expect(page.getByRole("heading", { name: "SIGN-IN METHODS" })).toBeVisible();
  await shoot(page, "settings", found);

  // House switcher sheet (a second house)
  await call(page, "POST", "/houses", {
    name: "Beach house",
    roommates: [],
    timezone: "America/New_York",
  });
  await open(page, "/");
  await page.getByRole("button", { name: /Switch house/ }).click();
  await expect(page.getByRole("dialog", { name: "Your houses" })).toBeVisible();
  await shoot(page, "house-switcher", found, false);

  expect(found).toEqual([]);
  await closeApt(apt);
  await context.close();
});
