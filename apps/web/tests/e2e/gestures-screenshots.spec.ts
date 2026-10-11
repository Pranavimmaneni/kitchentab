import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { call } from "./fixtures/app";
import { addLeftovers, aptWith, closeApt, open, seedShopRite, tile } from "./fixtures/household";
import { bin, bubble, centreOf, finger, hold, iconOf, phase } from "./fixtures/gestures";

/**
 * VIS-G1: the gesture states beside docs/design/renders/gestures/{r2,m2,s2,c3,sort-after}.png, at
 * 390 px in light and dark, written to apps/web/test-results/screens/gestures-{state}-{theme}.png
 * for review (Chromium only, by the `screenshots.spec.ts` ignore rule). Each state is held while
 * both themes are shot, so the picture is the live gesture, not a mock.
 */
const OUT = fileURLToPath(new URL("../../test-results/screens/", import.meta.url));

async function shoot(page: Page, state: string): Promise<void> {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${OUT}gestures-${state}-${theme}.png` });
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("VIS-G1 reorder, quick actions, select mode, collecting and stocked at 390 px in both themes [FR-G01] [FR-G03] [FR-G05] [FR-G06] [FR-G10]", async ({
  browser,
  baseURL,
  browserName,
}) => {
  test.setTimeout(240_000);
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    hasTouch: true,
    isMobile: true,
  });
  const page = await context.newPage();
  const apt = await aptWith(browser, baseURL, page, ["meera"]);
  await seedShopRite(page, apt);
  for (const [name, icon_key] of [
    ["Dumplings", "dumplings"],
    ["Ice cream", "ice_cream"],
  ] as const) {
    await addLeftovers(page, apt, { name, place: "freezer", icon_key });
  }
  await open(page, "/kitchen/fridge");
  const f = await finger(page, browserName);

  // r2: Eggs lifted and dragged between Milk and Yogurt; neighbours slide aside.
  const eggs = await hold(page, f, tile(page, "Eggs 18 ct"));
  await f.move({ x: eggs.x + 2, y: eggs.y + 24 }, 3);
  await expect.poll(() => phase(page)).toBe("dragging");
  const second = (await page.locator('[data-kt-area="fridge"] .kt-tile').nth(1).boundingBox())!;
  await f.move({ x: second.x + second.width * 0.12, y: second.y + 22 }, 8);
  await expect(page.locator(".kt-tile[data-shifted]").first()).toBeVisible();
  await shoot(page, "r2");
  await page.keyboard.press("Escape");
  await f.up();

  // m2: Yogurt lifted, the fan open, the finger on Pantry.
  await hold(page, f, tile(page, "Greek yogurt 32 oz"));
  await f.move(await centreOf(bubble(page, "Pantry")), 8);
  await expect(page.getByText("MOVE TO PANTRY")).toBeVisible();
  await shoot(page, "m2");
  await page.keyboard.press("Escape");
  await f.up();

  // s2: select mode with three picked.
  await page.getByRole("button", { name: "Select items to move" }).click();
  for (const name of ["Whole milk 1 gal", "Greek yogurt 32 oz", "Chicken thighs 3 lb"]) {
    await tile(page, name).click();
  }
  await expect(page.getByRole("toolbar", { name: /3 selected/ })).toBeVisible();
  await shoot(page, "s2");
  await page.getByRole("button", { name: "Done selecting" }).click();

  // c3: two collected, over the Pantry bin.
  const milk = await hold(page, f, tile(page, "Whole milk 1 gal"));
  await f.move({ x: milk.x + 2, y: milk.y + 24 }, 3);
  await f.move(await centreOf(iconOf(tile(page, "Greek yogurt 32 oz"))), 8);
  await page.waitForTimeout(400);
  await expect(page.locator(".kt-lifted__count")).toHaveText("2");
  await f.move(await centreOf(bin(page, "pantry")), 10);
  await expect(bin(page, "pantry")).toHaveAttribute("data-over", "");
  await shoot(page, "c3");
  await page.keyboard.press("Escape");
  await f.up();

  // sort-after: Review & send right after the food landed.
  const bill = await call<{ id: string }>(page, "POST", `/houses/${apt.id}/bills`, {
    store_name: "ShopRite",
    purchase_date: "2026-10-09",
  });
  const changes = [];
  for (const [name, cents, storage, icon_key] of [
    ["Frozen dumplings", 899, "freezer", "dumplings"],
    ["Ice cream", 649, "freezer", "ice_cream"],
    ["Whole milk", 429, "fridge", "milk"],
    ["Greek yogurt", 679, "fridge", "yogurt"],
    ["Eggs, 18 ct", 549, "fridge", "eggs"],
    ["Chicken thighs", 1137, "fridge", "chicken"],
    ["Jasmine rice", 999, "pantry", "rice"],
    ["Ground coffee", 849, "pantry", "coffee"],
  ] as const) {
    const item = await call<{ id: string }>(page, "POST", `/bills/${bill.id}/items`, {
      name,
      line_total_cents: cents,
      storage,
      icon_key,
    });
    changes.push({ item_id: item.id, participant: `m:${apt.member.pranav}`, on: true });
  }
  await call(page, "PUT", `/bills/${bill.id}/allocations`, { changes });
  let release!: () => void;
  const held = new Promise<void>((r) => (release = r));
  await page.route(`**/api/v1/bills/${bill.id}/send`, async (route) => {
    await held;
    await route.continue();
  });
  await open(page, `/bills/${bill.id}/send`);
  await page.locator(".kt-stocking").scrollIntoViewIfNeeded();
  await shoot(page, "sort-before");
  await page.getByRole("button", { name: /Send it/ }).click();
  await expect(page.locator(".kt-stock-bin__count")).toHaveCount(3);
  await page.waitForTimeout(300);
  await shoot(page, "sort-after");
  release();
  await page.waitForURL(`**/bills/${bill.id}/share`);

  await closeApt(apt);
  await context.close();
});
