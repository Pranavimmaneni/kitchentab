import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { chromium, expect, test } from "@playwright/test";
import { createHouse, seedShopRite, uniqueEmail } from "./fixtures/app";
import { apiSignIn } from "./fixtures/household";

/**
 * PWA-12 and PERF-03: Lighthouse (mobile) against the production build. PWA-12: performance, accessibility and best
 * practices ≥ 90 and the PWA audit passing. Lighthouse 11.7.1 is pinned because 12+ dropped the PWA
 * category. It needs a quiet machine (performance is measured with CPU throttling, so other test
 * suites running beside it skew it), so it runs only with KT_LIGHTHOUSE=1:
 *
 *   KT_LIGHTHOUSE=1 pnpm exec playwright test lighthouse --project=chromium-pixel-7
 */

const LIGHTHOUSE = "lighthouse@11.7.1";

test.skip(({ browserName }) => browserName !== "chromium", "Lighthouse drives Chromium");
test.skip(!process.env.KT_LIGHTHOUSE, "Lighthouse needs a quiet machine: set KT_LIGHTHOUSE=1");

/** Runs the pinned Lighthouse (mobile) on `url`; returns the parsed JSON report. */
function lighthouse(url: string, categories: string, out: string): LighthouseReport {
  mkdirSync(path.dirname(out), { recursive: true });
  const windows = process.platform === "win32";
  const args = [
    "-y",
    LIGHTHOUSE,
    url,
    `--only-categories=${categories}`,
    "--form-factor=mobile",
    "--chrome-flags=--headless=new --no-sandbox",
    "--output=json",
    "--output=html",
    `--output-path=${out}`,
    "--quiet",
  ];
  // npx.cmd needs a shell on Windows, which joins arguments unescaped: quote any with spaces (the
  // repo may live under a folder like "Untitled Folder").
  const quote = (a: string) => (windows && /[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a);
  execFileSync(windows ? "npx.cmd" : "npx", args.map(quote), {
    env: { ...process.env, CHROME_PATH: chromium.executablePath() },
    stdio: "inherit",
    shell: windows,
  });
  return JSON.parse(readFileSync(`${out}.report.json`, "utf8")) as LighthouseReport;
}

type LighthouseReport = {
  categories: Record<string, { score: number }>;
  audits: Record<string, { numericValue?: number }>;
};

test("PWA-12 Lighthouse mobile: performance, accessibility, best practices ≥ 90 and the PWA audit passes [FR-01]", async ({
  baseURL,
}, info) => {
  test.setTimeout(300_000);
  const report = lighthouse(
    `${baseURL}/start`,
    "performance,accessibility,best-practices,pwa",
    info.outputPath("lighthouse"),
  );
  const scores = Object.fromEntries(
    Object.entries(report.categories).map(([k, v]) => [k, v.score]),
  );
  expect(scores.performance).toBeGreaterThanOrEqual(0.9);
  expect(scores.accessibility).toBeGreaterThanOrEqual(0.9);
  expect(scores["best-practices"]).toBeGreaterThanOrEqual(0.9);
  expect(scores.pwa).toBe(1);
});

test("PERF-03 the bill link page on a throttled phone: LCP < 2.5 s, CLS < 0.1, TBT < 200 ms (the lab stand-in for INP) [FR-31]", async ({
  page,
  baseURL,
}, info) => {
  test.setTimeout(300_000);
  await apiSignIn(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { linkToken } = await seedShopRite(page, house, { split: "all", send: true });
  const report = lighthouse(
    `${baseURL}/b/${linkToken}`,
    "performance",
    info.outputPath("lighthouse-link"),
  );
  const metric = (id: string) => report.audits[id]?.numericValue ?? Number.POSITIVE_INFINITY;
  console.log(
    `PERF-03 link page: LCP ${metric("largest-contentful-paint").toFixed(0)} ms, ` +
      `CLS ${metric("cumulative-layout-shift").toFixed(3)}, TBT ${metric("total-blocking-time").toFixed(0)} ms`,
  );
  expect(metric("largest-contentful-paint")).toBeLessThan(2500);
  expect(metric("cumulative-layout-shift")).toBeLessThan(0.1);
  expect(metric("total-blocking-time")).toBeLessThan(200);
});
