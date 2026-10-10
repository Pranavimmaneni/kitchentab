import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { call, enterCode, hydrated, seedShopRite, uniqueEmail, type Roomie } from "./fixtures/app";

/**
 * Review screenshots of the Milestone 3–4 screens at 390 px in light and dark (Chromium only;
 * matched by the `screenshots.spec.ts` ignore rule for the other projects). Written to
 * apps/web/test-results/screens/{screen}-{theme}.png for comparison with docs/design renders.
 */
const OUT = fileURLToPath(new URL("../../test-results/screens/", import.meta.url));

/** The viewport grows to the page height so sticky bars sit at the bottom, as on a phone. */
async function shoot(page: Page, name: string): Promise<void> {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.evaluate(() => document.fonts.ready);
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width: 390, height: Math.max(844, height) });
    await page.screenshot({ path: `${OUT}${name}-${theme}.png` });
    await page.setViewportSize({ width: 390, height: 844 });
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("screenshots of screens A, B, C, D, 2, 3, 4 and E at 390 px in both themes", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(180_000);
  const options = {
    baseURL,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    reducedMotion: "reduce" as const,
  };
  const context = await browser.newContext(options);
  const page = await context.newPage();

  // A · Start
  await page.goto("/start");
  await hydrated(page);
  await expect(page.getByRole("button", { name: "Send me a code →" })).toBeVisible();
  await shoot(page, "A-start");

  // B · Verify
  const email = uniqueEmail("pranav");
  await page.getByLabel("YOUR EMAIL").fill(email);
  await page.getByRole("button", { name: "Send me a code →" }).click();
  await page.waitForURL("**/verify");
  await hydrated(page);
  await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  await shoot(page, "B-verify");

  // C · Who lives here
  await enterCode(page, email);
  await page.waitForURL("**/houses/new");
  await expect(page.getByTestId("invite-link")).toContainText("/h/");
  await page.getByLabel("HOUSE NAME").fill("Apt 4B");
  for (const name of ["Arjun", "Meera", "Kabir"]) {
    await page.getByRole("textbox", { name: "Add a roommate" }).fill(name);
    await page.getByRole("textbox", { name: "Add a roommate" }).press("Enter");
    await expect(page.getByRole("button", { name: `Remove ${name}` })).toBeVisible();
  }
  await page.waitForTimeout(1200); // name autosave
  await shoot(page, "C-house");

  const me = await call<{ houses: { id: string }[] }>(page, "GET", "/me");
  const view = await call<{
    members: { id: string; display_name: string }[];
    me: { member_id: string };
  }>(page, "GET", `/houses/${me.houses[0]!.id}`);
  const id = (n: string) => view.members.find((m) => m.display_name === n)!.id;
  const house = {
    id: me.houses[0]!.id,
    member: {
      pranav: view.me.member_id,
      arjun: id("Arjun"),
      meera: id("Meera"),
      kabir: id("Kabir"),
    } as Record<Roomie, string>,
  };

  // 2 · Review
  const review = await seedShopRite(page, house);
  await page.goto(`/bills/${review.billId}/review`);
  await expect(page.getByText("*** MATHS CHECKS OUT ***")).toBeVisible();
  await shoot(page, "2-review");

  // 3 · Split, 9 of 12 assigned like Split.dc.html
  const split = await seedShopRite(page, house, {
    split: "all",
    leave: ["spinach", "bars", "coffee"],
  });
  await page.goto(`/bills/${split.billId}/split`);
  await expect(page.getByText("3 ITEMS LOOKING FOR A HOME")).toBeVisible();
  await shoot(page, "3-split");

  // 4 · Here's the damage, then E · Share after sending
  const sent = await seedShopRite(page, house, { split: "all" });
  await page.goto(`/bills/${sent.billId}/send`);
  await expect(page.getByText("$71.67")).toBeVisible();
  await shoot(page, "4-send");
  await page.getByRole("button", { name: "Send it to the house ↗" }).click();
  await page.waitForURL(/\/share$/);
  await expect(page.getByText("SPLIT 4 WAYS · TAP FOR YOUR SHARE")).toBeVisible();
  await shoot(page, "E-share");
  const bill = await call<{ bill: { link_token: string } }>(page, "GET", `/bills/${sent.billId}`);

  // D · Join from the bill link, signed out, Meera picked
  const visitor = await browser.newContext(options);
  const join = await visitor.newPage();
  await join.goto(`/b/${bill.bill.link_token}`);
  await hydrated(join);
  await join.getByRole("button", { name: "Meera", exact: true }).click();
  await expect(join.getByText("Hi Meera, quick check it's you:")).toBeVisible();
  await shoot(join, "D-join");

  await visitor.close();
  await context.close();
});
