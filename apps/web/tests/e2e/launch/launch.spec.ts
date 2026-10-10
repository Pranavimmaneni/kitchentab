import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { call, createHouse, hydrated, uniqueEmail } from "../fixtures/app";
import { BLOB_ORIGIN, REMOTE, WEB_ORIGIN } from "../fixtures/env";
import { apiSignIn } from "../fixtures/household";

/**
 * The browser-only half of `make launch-check` (BUILD_ALL §6 step 2; scripts/launch_check.py does
 * the rest over HTTP): install criteria and service-worker registration over real HTTPS, and one
 * photo receipt read by the browser's own OCR (tesseract.js from /ocr/) through the capture screen.
 * Runs only against a deployed origin: TARGET=local-vercel (the rehearsal) or prod.
 */
test.skip(!REMOTE, "launch checks run against TARGET=local-vercel or TARGET=prod");
test.skip(
  ({ browserName }) => browserName !== "chromium",
  "install criteria and CDP are Chromium's",
);
// The suite blocks service workers by default (playwright.config.ts); these checks need the real one.
test.use({ serviceWorkers: "allow" });

const RECEIPTS = resolve(process.cwd(), "../../packages/fixtures/receipts");
const PHOTO = "i01"; // ShopRite; i01.truth.json has the printed total

/** Every request the page makes, to prove nothing goes to a third-party origin. */
function recordOrigins(page: Page): Set<string> {
  const seen = new Set<string>();
  page.on("request", (r) => seen.add(new URL(r.url()).origin));
  return seen;
}

test("PWA-01 PWA-02 installable over HTTPS: secure context, manifest, service worker controls the page, no installability errors @launch", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/start");
  expect(await page.evaluate(() => window.isSecureContext)).toBe(true);
  expect(new URL(page.url()).protocol).toBe("https:");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
    "href",
    /manifest\.webmanifest/,
  );

  // The app registers its worker itself (lib/pwa/register.ts); wait until one is active for "/".
  const reg = await page.evaluate(async () => {
    const ready = await Promise.race([
      navigator.serviceWorker.ready,
      new Promise<null>((r) => setTimeout(() => r(null), 30_000)),
    ]);
    return ready ? { scope: ready.scope, script: ready.active?.scriptURL ?? "" } : null;
  });
  expect(reg, "a service worker registered and activated").not.toBeNull();
  expect(reg!.scope).toBe(`${WEB_ORIGIN}/`);
  expect(reg!.script).toMatch(/\/sw(-[A-Za-z0-9-]+)?\.js$/);

  // Chromium's own install criteria (what decides whether "Install" is offered).
  await page.reload();
  const cdp = await page.context().newCDPSession(page);
  const { installabilityErrors } = (await cdp.send("Page.getInstallabilityErrors")) as {
    installabilityErrors: { errorId: string }[];
  };
  expect(installabilityErrors.map((e) => e.errorId)).toEqual([]);
  const manifest = (await cdp.send("Page.getAppManifest")) as { url: string; errors: unknown[] };
  expect(manifest.errors).toEqual([]);
});

test("EXTRACT-12 a photo receipt is read by browser OCR from /ocr/ and lands on the draft with the printed total @launch [FR-11]", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const truth = JSON.parse(readFileSync(resolve(RECEIPTS, `${PHOTO}.truth.json`), "utf8")) as {
    total_cents: number;
  };
  await page.goto("/start");
  await apiSignIn(page, uniqueEmail("pranav"));
  const house = await createHouse(page, "Launch check OCR");
  const origins = recordOrigins(page);

  await page.goto(`/bills/new?house=${house.id}`);
  await hydrated(page); // a file picked before hydration would never reach React's onChange
  // The sheet's buttons stay disabled until the house is known (GET /me); then pick a photo with
  // the photo-library input that "Choose photo or PDF" opens.
  await expect(
    page.getByRole("dialog").getByRole("button", { name: /choose photo/i }),
  ).toBeEnabled();
  const input = page.locator('input[type="file"][accept*="image"]:not([capture])').first();
  await input.setInputFiles(resolve(RECEIPTS, `${PHOTO}.jpg`));
  await page.waitForURL(/\/bills\/[0-9a-f-]+\/review/, { timeout: 30_000 });
  const billId = /\/bills\/([0-9a-f-]+)\/review/.exec(page.url())![1]!;

  // OCR runs on this device, then the lines go to POST /bills/{id}/ocr; poll the draft.
  let view: { items: unknown[]; bill: { receipt_total_cents: number | null } } | undefined;
  await expect(async () => {
    view = await call(page, "GET", `/bills/${billId}`);
    expect(view!.items.length).toBeGreaterThan(0);
  }).toPass({ timeout: 150_000, intervals: [1_000, 2_000, 5_000] });
  expect(view!.bill.receipt_total_cents).toBe(truth.total_cents);

  // tesseract's worker, core and model came from this origin, nothing from a CDN.
  const ours = (o: string) =>
    o === WEB_ORIGIN ||
    o === BLOB_ORIGIN ||
    /^https:\/\/([a-z0-9-]+\.)*(blob\.vercel-storage\.com|vercel\.com)$/.test(o) || // Vercel Blob
    o === "null" ||
    o.startsWith("data:") ||
    o.startsWith("blob:");
  const foreign = [...origins].filter((o) => !ours(o));
  expect(foreign).toEqual([]);
});
