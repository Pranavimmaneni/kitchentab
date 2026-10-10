import { defineConfig, devices } from "@playwright/test";
import {
  API_ENV,
  API_ORIGIN,
  API_PORT,
  REMOTE,
  TARGET,
  WEB_ORIGIN,
  WEB_PORT,
  chromiumTrustArgs,
} from "./tests/e2e/fixtures/env";

const PORT = WEB_PORT;
// TARGET=local-vercel|prod: an already running HTTPS origin, no servers started here (ADR-0028).
const BASE_URL = REMOTE ? WEB_ORIGIN : `http://localhost:${PORT}`;
const LOCAL_VERCEL = TARGET === "local-vercel";
const UV = process.env.UV ?? "uv";
// Reusing a server already on the port is opt-in: a leftover API from an earlier run (or one started
// with another WEB_ORIGIN) silently answered every browser write with 403 "Cross-site request blocked".
const REUSE = process.env.KT_E2E_REUSE === "1";

/**
 * E2E projects per TEST_PLAN §1: WebKit (iPhone 14), Chromium (Pixel 7), Firefox desktop.
 * Two servers: the real API (APP_ENV=test, test inbox, fake SMS) on its own port and test
 * database, and a production build of the web app (component gallery enabled) that proxies
 * /api to it via KT_API_ORIGIN. Postgres and Redis come from docker compose.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  // Keep Playwright's own artifacts in a subfolder so test-results/*.png screenshots survive.
  outputDir: "./test-results/playwright",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    // The service worker would sit between page.route() and the network; specs that test it
    // (pwa, push, offline, receipts) opt in with test.use({ serviceWorkers: "allow" }).
    serviceWorkers: "block",
  },
  projects: [
    {
      name: "webkit-iphone-14",
      use: { ...devices["iPhone 14"], ...(LOCAL_VERCEL ? { ignoreHTTPSErrors: true } : {}) },
      testIgnore: /screenshots\.spec\.ts/,
    },
    {
      name: "chromium-pixel-7",
      use: {
        ...devices["Pixel 7"],
        ...(LOCAL_VERCEL ? { launchOptions: { args: chromiumTrustArgs() } } : {}),
      },
    },
    {
      name: "firefox-desktop",
      use: { ...devices["Desktop Firefox"], ...(LOCAL_VERCEL ? { ignoreHTTPSErrors: true } : {}) },
      testIgnore: /screenshots\.spec\.ts/,
    },
  ],
  webServer: REMOTE
    ? []
    : [
        {
          command: `${UV} run alembic upgrade head && ${UV} run uvicorn --factory app.main:create_app --port ${API_PORT}`,
          cwd: "../api",
          url: `${API_ORIGIN}/api/v1/health`,
          reuseExistingServer: REUSE,
          timeout: 180_000,
          env: API_ENV,
          stdout: "ignore",
          stderr: "pipe",
        },
        {
          command: `pnpm run build && pnpm exec next start -H 127.0.0.1 --port ${PORT}`,
          url: `${BASE_URL}/dev/components`,
          reuseExistingServer: REUSE,
          timeout: 300_000,
          env: { KT_DEV_COMPONENTS: "1", KT_API_ORIGIN: API_ORIGIN },
          stdout: "ignore",
          stderr: "pipe",
        },
      ],
});
