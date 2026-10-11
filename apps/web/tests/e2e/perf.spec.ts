import { existsSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { createHouse, hydrated, seedShopRite, uniqueEmail } from "./fixtures/app";
import { addLeftovers, apiSignIn, aptWith, closeApt } from "./fixtures/household";
import { receiptFile, waitForReading } from "./fixtures/receipts";

/**
 * PERF-04, PERF-05 and PERF-06 (TEST_PLAN 3.12). Chromium only: request sizes and CPU throttling come from
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

const DATA_URL = /\/api\/v1\/houses\/[^/]+\/(inventory|kitchen)/;

/**
 * The page watches itself: every animation frame it checks whether the screen named in
 * sessionStorage is on show and records the first frame where it is. Both ends come from the
 * page's own clock (the data response's `responseEnd` from Resource Timing), so Playwright's
 * polling steps (100, 250, 500, 1000 ms) and the hop to Node don't land in the number.
 */
const PROBE = `(() => {
  const screens = {
    fridge: () => document.querySelector('[role="group"][aria-label="refrigerated, 200 items" i]'),
    kitchen: () => Array.from(document.querySelectorAll("a")).some((a) =>
      /^Open the fridge: 200 items/.test(a.getAttribute("aria-label") || a.textContent || "")),
  };
  const shown = screens[sessionStorage.getItem("ktPerfProbe")];
  if (!shown) return;
  const tick = () => {
    if (shown()) window.__ktShownAt = performance.now();
    else requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})()`;

/** Milliseconds from the inventory response to `ready` on screen, with the CPU slowed 4x. */
async function renderAfterData(
  page: Page,
  path: string,
  screen: "fridge" | "kitchen",
  ready: () => Promise<void>,
): Promise<number> {
  await page.evaluate((name) => sessionStorage.setItem("ktPerfProbe", name), screen);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 }); // Lighthouse's mid-tier mobile
  const response = page.waitForResponse((r) => DATA_URL.test(r.url()));
  await page.goto(path);
  await response;
  await ready();
  await page.waitForFunction(() => "__ktShownAt" in window);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
  const { shownAt, dataAt } = await page.evaluate((source) => {
    const re = new RegExp(source);
    const data = performance
      .getEntriesByType("resource")
      .filter((e) => re.test(e.name)) as PerformanceResourceTiming[];
    sessionStorage.removeItem("ktPerfProbe");
    return {
      shownAt: (window as unknown as { __ktShownAt: number }).__ktShownAt,
      dataAt: data.length ? data[data.length - 1]!.responseEnd : NaN,
    };
  }, DATA_URL.source);
  expect(dataAt, `Resource Timing saw the data request on ${path}`).not.toBeNaN();
  return Math.round(shownAt - dataAt);
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
  await page.addInitScript(PROBE);
  await page.goto("/kitchen/fridge");
  await hydrated(page);

  const fridge = await renderAfterData(page, "/kitchen/fridge", "fridge", async () => {
    await expect(page.getByRole("button", { name: "ALL 200" })).toBeVisible();
    await expect(page.getByRole("group", { name: "refrigerated, 200 items" })).toBeVisible();
  });
  const kitchen = await renderAfterData(page, "/", "kitchen", async () => {
    await expect(page.getByRole("link", { name: /^Open the fridge: 200 items/ })).toBeVisible();
  });
  console.log(`PERF-06 render after data, CPU 4x: fridge ${fridge} ms, kitchen ${kitchen} ms`);
  expect(fridge, "fridge with 200 items").toBeLessThan(1000);
  expect(kitchen, "kitchen with 200 items").toBeLessThan(1000);
  await closeApt(apt);
});

test("PERF-05 reading a receipt on the phone: p90 under 15 s from picking the photo to the review screen [FR-10] [FR-11]", async ({
  page,
}) => {
  test.setTimeout(600_000);
  await apiSignIn(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  // Ten photo receipts (the C1 set's photos), each read on the device like E2E-01.
  const ids = ["i01", "i02", "i03", "i04", "i07", "i08", "i09", "i11", "i13", "i14"];
  const seconds: number[] = [];
  for (const id of ids) {
    await page.goto(`/bills/new?house=${house.id}`);
    await hydrated(page);
    await page.locator("[data-kt-capture-ready]").waitFor({ state: "attached" });
    const started = Date.now();
    await page
      .locator('input[data-kt-input="library"]')
      .setInputFiles(receiptFile(fixtureFile(id)));
    expect(await waitForReading(page, 120_000), id).toBe("review");
    seconds.push((Date.now() - started) / 1000);
  }
  const sorted = [...seconds].sort((a, b) => a - b);
  const p90 = sorted[Math.ceil(sorted.length * 0.9) - 1]!;
  console.log(
    `PERF-05 reading times (s): ${seconds.map((s) => s.toFixed(1)).join(", ")}; p90 ${p90.toFixed(1)}`,
  );
  expect(p90).toBeLessThan(15);
});

function fixtureFile(id: string): string {
  for (const ext of [".jpg", ".png", ".pdf"]) {
    if (existsSync(receiptFile(`${id}${ext}`))) return `${id}${ext}`;
  }
  throw new Error(`no fixture file for ${id}`);
}
