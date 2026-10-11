import { expect, test, type Page } from "@playwright/test";
import { createHouse, hydrated, seedShopRite, uniqueEmail } from "./fixtures/app";
import { addLeftovers, apiSignIn, aptWith, closeApt } from "./fixtures/household";

/**
 * PERF-04 and PERF-06 (TEST_PLAN 3.12). Chromium only: request sizes and CPU throttling come from
 * the DevTools protocol. Lighthouse budgets (PERF-03, PWA-12) need a quiet machine and stay behind
 * KT_LIGHTHOUSE.
 */

test.skip(({ browserName }) => browserName !== "chromium", "DevTools protocol: Chromium");

test("PERF-04 the bill link page ships under 150 KB of JavaScript, gzipped [FR-31]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(120_000);
  await apiSignIn(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { linkToken } = await seedShopRite(page, house, { split: "all", send: true });

  // A roommate who has never opened KitchenTab: signed out, empty cache.
  const visitor = await browser.newContext({ baseURL, serviceWorkers: "block" });
  const link = await visitor.newPage();
  const scripts: Promise<number>[] = [];
  link.on("requestfinished", (request) => {
    if (request.resourceType() === "script")
      scripts.push(request.sizes().then((s) => s.responseBodySize));
  });
  await link.goto(`/b/${linkToken}`);
  await hydrated(link);
  await link.waitForLoadState("networkidle");
  const bytes = (await Promise.all(scripts)).reduce((a, b) => a + b, 0);
  console.log(
    `PERF-04 link page JS: ${(bytes / 1024).toFixed(1)} KB over the wire (${scripts.length} files)`,
  );
  expect(bytes, "gzipped JS on /b/[token]").toBeLessThan(150 * 1024);
  await visitor.close();
});

/** Milliseconds from the inventory response to `ready` on screen, with the CPU slowed 4x. */
async function renderAfterData(
  page: Page,
  path: string,
  ready: () => Promise<void>,
): Promise<number> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 }); // Lighthouse's mid-tier mobile
  const data = page
    .waitForResponse((r) => /\/api\/v1\/houses\/[^/]+\/(inventory|kitchen)/.test(r.url()))
    .then(() => Date.now());
  await page.goto(path);
  await ready();
  const shown = Date.now();
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
  return shown - (await data);
}

test("PERF-06 a kitchen of 200 items renders in under a second on a mid-range phone [FR-63] [FR-65]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(300_000);
  // Four roommates stock it (50 each) so nobody hits the 60 writes a minute limit.
  const apt = await aptWith(browser, baseURL, page, ["arjun", "meera", "kabir"]);
  const stockers = [page, apt.pages.arjun!, apt.pages.meera!, apt.pages.kabir!];
  await Promise.all(
    stockers.map(async (who, s) => {
      for (let i = 0; i < 50; i++) {
        const n = String(s * 50 + i + 1).padStart(3, "0");
        await addLeftovers(who, apt, { name: `Leftover ${n}`, place: "fridge" });
      }
    }),
  );
  // Warm the app shell once, so the measurement is the screen, not the first download.
  await page.goto("/kitchen/fridge");
  await hydrated(page);

  const fridge = await renderAfterData(page, "/kitchen/fridge", async () => {
    await expect(page.getByRole("button", { name: "ALL 200" })).toBeVisible();
    await expect(page.getByRole("group", { name: "refrigerated, 200 items" })).toBeVisible();
  });
  const kitchen = await renderAfterData(page, "/", async () => {
    await expect(page.getByRole("link", { name: /^Open the fridge: 200 items/ })).toBeVisible();
  });
  console.log(`PERF-06 render after data, CPU 4x: fridge ${fridge} ms, kitchen ${kitchen} ms`);
  expect(fridge, "fridge with 200 items").toBeLessThan(1000);
  expect(kitchen, "kitchen with 200 items").toBeLessThan(1000);
  await closeApt(apt);
});
