import { expect, test, type Page } from "@playwright/test";
import { call, uniqueEmail } from "./fixtures/app";
import { apiSignIn, houseDate } from "./fixtures/household";
import { quietPrompts } from "./fixtures/quiet";

/** Screen 3 with a big house (PRD §14A 3 "Many members"): phones on every engine. */

async function settle(page: Page): Promise<void> {
  await page.locator("html[data-hydrated]").waitFor({ state: "attached" });
  await expect(page.locator("h1").first()).toBeVisible();
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 15_000 });
}

test("SPLIT-10 with 6+ members the checkbox columns scroll sideways while the item column stays put, at 390 px [FR-21] [FR-22]", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(180_000);
  const context = await browser.newContext({ baseURL, viewport: { width: 390, height: 844 } });
  await quietPrompts(context);
  const page = await context.newPage();
  await apiSignIn(page, uniqueEmail("pranav"));
  const roommates = ["Arjun", "Meera", "Kabir", "Isha", "Rohan", "Tara"];
  const created = await call<{
    house: { id: string };
    members: { id: string; display_name: string }[];
  }>(page, "POST", "/houses", { name: "Big house", roommates, timezone: "America/New_York" });
  const byName = Object.fromEntries(created.members.map((m) => [m.display_name, m.id]));
  const bill = await call<{ id: string }>(page, "POST", `/houses/${created.house.id}/bills`, {
    store_name: "Costco",
    purchase_date: await houseDate(page),
  });
  for (const [name, cents] of [
    ["Rotisserie chicken", 499],
    ["Paper towels 12 pk", 2399],
    ["Sparkling water 24 ct", 1099],
  ] as const) {
    await call(page, "POST", `/bills/${bill.id}/items`, {
      name,
      line_total_cents: cents,
      storage: "none",
    });
  }
  await page.goto(`/bills/${bill.id}/split`);
  await settle(page);
  const grid = page.getByRole("grid", { name: "Who's eating what" });
  await expect(grid.getByRole("columnheader")).toHaveCount(1 + 1 + roommates.length);
  await expect(page.locator(".kt-split")).toHaveAttribute("data-wide", "true");

  // The page itself never scrolls sideways (no inner panel for the page, VIS-03) …
  const page_ = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(page_).toBeLessThanOrEqual(0);
  // … the checkbox columns do, inside the grid body.
  const body = page.locator(".kt-split__body");
  const room = await body.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(room).toBeGreaterThan(40);
  const name = grid.getByRole("rowheader").first();
  const tara = grid.getByRole("button", { name: "Tara uses Rotisserie chicken", exact: true });
  const before = { name: (await name.boundingBox())!, tara: (await tara.boundingBox())! };
  await body.evaluate((el) => el.scrollTo({ left: el.scrollWidth }));
  await expect.poll(async () => (await tara.boundingBox())!.x).toBeLessThan(before.tara.x - 20);
  const after = (await name.boundingBox())!;
  expect(Math.abs(after.x - before.name.x)).toBeLessThan(1);
  // The far column is now on screen and usable; the header scrolled with it.
  await tara.click();
  await expect(tara).toHaveAttribute("aria-pressed", "true");
  const head = await page.locator(".kt-split__head").evaluate((el) => el.scrollLeft);
  expect(head).toBeGreaterThan(0);
  expect(byName.Tara).toBeTruthy();
  await context.close();
});
