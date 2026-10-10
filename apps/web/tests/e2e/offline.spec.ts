import { expect, test, type Request } from "@playwright/test";
import { call, createHouse, hydrated, seedShopRite, uniqueEmail } from "./fixtures/app";
import { apiSignIn } from "./fixtures/household";

/**
 * Offline with the service worker in place (PRD §20.1 Offline, §21): saved screens open read-only
 * with the banner, every write control says "Needs a connection", nothing is queued, and coming
 * back online catches up. (OFFLINE-03/04 at the write layer live in sync.spec.ts.)
 */

test.skip(({ browserName }) => browserName !== "chromium", "service worker offline: Chromium");
test.use({ serviceWorkers: "allow" });

test("OFFLINE-01 OFFLINE-02 PWA-02 a saved bill opens offline read-only with the banner; back online it catches up [FR-32] [FR-50]", async ({
  page,
  context,
}) => {
  test.setTimeout(120_000);
  await apiSignIn(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { billId, itemIds } = await seedShopRite(page, house, { split: "all", send: true });

  // Visit once online: the worker saves the screen and its reads.
  await page.goto(`/bills/${billId}`);
  await hydrated(page);
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller), undefined, {
    timeout: 30_000,
  });
  await page.reload();
  await hydrated(page);
  await page.getByRole("tab", { name: "Items & splits" }).click();
  await expect(page.getByText("Whole milk 1 gal").first()).toBeVisible();

  // Writes this page sends while offline, and anything replayed afterwards.
  const writes: string[] = [];
  let phase = "offline";
  page.on("request", (r: Request) => {
    if (r.method() !== "GET" && r.url().includes("/api/"))
      writes.push(`${phase} ${r.method()} ${new URL(r.url()).pathname}`);
  });

  await context.setOffline(true);
  await page.reload();
  await hydrated(page);
  await expect(page.getByText("YOU'RE OFFLINE · SHOWING SAVED TABS")).toBeVisible();
  await expect(page.getByRole("heading", { name: "WHO OWES WHAT" })).toBeVisible(); // this device's saved copy
  await page.getByRole("tab", { name: "Items & splits" }).click();
  await expect(page.getByText("Whole milk 1 gal").first()).toBeVisible();
  await expect(page.getByText("Needs a connection").first()).toBeVisible();
  const remind = page.getByRole("button", { name: /Remind|Stamp it settled/ }).first();
  if (await remind.count()) await expect(remind).toBeDisabled();

  // Back online; meanwhile the bill changed on another device.
  phase = "online";
  await context.setOffline(false);
  const fresh = await call<{ bill: { version: number }; items: { id: string; version: number }[] }>(
    page,
    "GET",
    `/bills/${billId}`,
  );
  const milk = fresh.items.find((i) => i.id === itemIds.milk)!;
  await call(
    page,
    "PATCH",
    `/bills/${billId}/items/${itemIds.milk}`,
    { name: "Whole milk 2 gal" },
    { ifMatch: milk.version },
  );
  await expect(page.getByText("YOU'RE OFFLINE · SHOWING SAVED TABS")).toBeHidden();
  await expect(page.getByText("Whole milk 2 gal").first()).toBeVisible({ timeout: 15_000 });
  // Nothing was queued: no write left this page offline, and none was replayed on reconnect (the
  // only one allowed is the set-state "seen" marker the open bill always sends on load).
  expect(writes.filter((w) => w.startsWith("offline"))).toEqual([]);
  expect(writes.filter((w) => !/^online PUT \/api\/v1\/bills\/[^/]+\/seen$/.test(w))).toEqual([]);
});

test("OFFLINE-01 PWA-02 a screen never opened on this device shows the offline page, not an error [FR-50]", async ({
  page,
  context,
}) => {
  await apiSignIn(page, uniqueEmail("pranav"));
  await createHouse(page);
  await page.goto("/tabs");
  await hydrated(page);
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller), undefined, {
    timeout: 30_000,
  });
  await context.setOffline(true);
  await page.goto("/house").catch(() => undefined);
  await expect(page.getByRole("heading", { name: "You're offline." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await context.setOffline(false);
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page).toHaveURL(/\/house$/);
  await expect(page.getByRole("heading", { name: "You're offline." })).toBeHidden();
});
