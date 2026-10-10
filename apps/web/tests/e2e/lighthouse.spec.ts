import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { chromium, expect, test } from "@playwright/test";

/**
 * PWA-12: Lighthouse (mobile) against the production build: performance, accessibility and best
 * practices ≥ 90 and the PWA audit passing. Lighthouse 11.7.1 is pinned because 12+ dropped the PWA
 * category. It needs a quiet machine (performance is measured with CPU throttling, so other test
 * suites running beside it skew it), so it runs only with KT_LIGHTHOUSE=1:
 *
 *   KT_LIGHTHOUSE=1 pnpm exec playwright test lighthouse --project=chromium-pixel-7
 */

const LIGHTHOUSE = "lighthouse@11.7.1";

test.skip(({ browserName }) => browserName !== "chromium", "Lighthouse drives Chromium");
test.skip(!process.env.KT_LIGHTHOUSE, "Lighthouse needs a quiet machine: set KT_LIGHTHOUSE=1");

test("PWA-12 Lighthouse mobile: performance, accessibility, best practices ≥ 90 and the PWA audit passes [FR-01]", async ({
  baseURL,
}, info) => {
  test.setTimeout(300_000);
  const out = info.outputPath("lighthouse");
  mkdirSync(path.dirname(out), { recursive: true });
  execFileSync(
    process.platform === "win32" ? "npx.cmd" : "npx",
    [
      "-y",
      LIGHTHOUSE,
      `${baseURL}/start`,
      "--only-categories=performance,accessibility,best-practices,pwa",
      "--form-factor=mobile",
      "--chrome-flags=--headless=new --no-sandbox",
      "--output=json",
      "--output=html",
      `--output-path=${out}`,
      "--quiet",
    ],
    {
      env: { ...process.env, CHROME_PATH: chromium.executablePath() },
      stdio: "inherit",
      shell: process.platform === "win32",
    },
  );
  const report = JSON.parse(readFileSync(`${out}.report.json`, "utf8")) as {
    categories: Record<string, { score: number }>;
  };
  const scores = Object.fromEntries(
    Object.entries(report.categories).map(([k, v]) => [k, v.score]),
  );
  expect(scores.performance).toBeGreaterThanOrEqual(0.9);
  expect(scores.accessibility).toBeGreaterThanOrEqual(0.9);
  expect(scores["best-practices"]).toBeGreaterThanOrEqual(0.9);
  expect(scores.pwa).toBe(1);
});
