import { expect, test, type Page } from "@playwright/test";
import { call } from "./fixtures/app";
import { aptWith, closeApt, open, seedShopRite, tile } from "./fixtures/household";

/**
 * Kitchen races as the roommates see them (PRD §21.7 SYNC-07, SYNC-13; TEST_PLAN §3.7 level 2):
 * two owners of the chicken (Pranav and Arjun) act at the same moment on separate phones. The
 * API race tests (apps/api tests/test_kitchen.py) cover the 200-iteration database side.
 */
test.describe.configure({ mode: "default" });

async function chickenId(page: Page, houseId: string): Promise<string> {
  const fridge = await call<{ areas: { items: { id: string; name: string }[] }[] }>(
    page,
    "GET",
    `/houses/${houseId}/inventory?place=fridge`,
  );
  return fridge.areas.flatMap((a) => a.items).find((i) => i.name.startsWith("Chicken"))!.id;
}

test("SYNC-07 two owners answer the same ask at once: the first answer stands, the second is told who answered, and both cards agree [FR-72] [FR-73] @multi", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun", "kabir"]);
  const { arjun, kabir } = apt.pages as Required<typeof apt.pages>;
  await seedShopRite(page, apt);
  const chicken = await chickenId(page, apt.id);
  await call(kabir, "POST", `/inventory/${chicken}/requests`, { amount: "a_bit" });

  await Promise.all([open(page, "/kitchen/requests"), open(arjun, "/kitchen/requests")]);
  const cardOf = (who: Page) =>
    who.getByRole("article", { name: "Kabir wants a bit of your chicken thighs" });
  await cardOf(arjun)
    .getByRole("radio", { name: /^Add them to the split/ })
    .click();
  await Promise.all([
    cardOf(page).getByRole("button", { name: "Say yes" }).click(),
    cardOf(arjun).getByRole("button", { name: "Say yes" }).click(),
  ]);

  // Exactly one of them hears "Already answered by …", naming the other.
  await expect
    .poll(
      async () =>
        (await page.getByText("Already answered by Arjun").count()) +
        (await arjun.getByText("Already answered by Pranav").count()),
      { timeout: 5000 },
    )
    .toBe(1);
  const stamp = /^YES · (ON THE HOUSE|ADDED TO SPLIT)$/;
  let shown = "";
  await expect(async () => {
    const mine = await cardOf(page).getByText(stamp).textContent({ timeout: 50 });
    const theirs = await cardOf(arjun).getByText(stamp).textContent({ timeout: 50 });
    expect(mine).toBe(theirs);
    shown = mine ?? "";
  }).toPass({ timeout: 2000 });
  // The asker sees the same outcome.
  const ask = (
    await call<{ requests: { status: string; resolution: string | null }[] }>(
      kabir,
      "GET",
      `/houses/${apt.id}/requests?box=out`,
    )
  ).requests[0]!;
  expect(ask.status).toBe("approved");
  expect(shown).toBe(ask.resolution === "split" ? "YES · ADDED TO SPLIT" : "YES · ON THE HOUSE");
  await closeApt(apt);
});

test("SYNC-13 two owners mark the same item used up at once: it leaves both fridges, and one Undo brings it back for both [FR-77] @multi", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun"]);
  const { arjun } = apt.pages as Required<typeof apt.pages>;
  await seedShopRite(page, apt);
  await Promise.all([open(page, "/kitchen/fridge"), open(arjun, "/kitchen/fridge")]);
  for (const who of [page, arjun]) await tile(who, "Chicken thighs 3 lb").click();
  const usedUp = (who: Page) =>
    who
      .getByRole("dialog", { name: "Chicken thighs 3 lb" })
      .getByRole("button", { name: "Used up" });
  await Promise.all([usedUp(page).click(), usedUp(arjun).click()]);

  for (const who of [page, arjun]) {
    try {
      await expect(tile(who, "Chicken thighs 3 lb")).toHaveCount(0, { timeout: 2000 });
    } catch (e) {
      console.log("DEBUG", who === page ? "pranav" : "arjun", (await who.locator("body").innerText()).slice(0, 1500));
      await who.waitForTimeout(3000);
      console.log("DEBUG later", await tile(who, "Chicken thighs 3 lb").count());
      throw e;
    }
  }
  // One Undo (from whichever toast) restores it everywhere.
  await page.getByRole("button", { name: "Undo" }).click();
  for (const who of [page, arjun]) {
    await expect(tile(who, "Chicken thighs 3 lb")).toBeVisible({ timeout: 2000 });
  }
  await closeApt(apt);
});
