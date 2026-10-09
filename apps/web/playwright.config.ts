import { defineConfig, devices } from "@playwright/test";
import { API_ENV, API_ORIGIN, API_PORT } from "./tests/e2e/fixtures/env";

const PORT = 3000;
const BASE_URL = `http://localhost:${PORT}`;
const UV = process.env.UV ?? "uv";

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
  },
  projects: [
    {
      name: "webkit-iphone-14",
      use: { ...devices["iPhone 14"] },
      testIgnore: /screenshots\.spec\.ts/,
    },
    {
      name: "chromium-pixel-7",
      use: { ...devices["Pixel 7"] },
    },
    {
      name: "firefox-desktop",
      use: { ...devices["Desktop Firefox"] },
      testIgnore: /screenshots\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command: `${UV} run alembic upgrade head && ${UV} run uvicorn --factory app.main:create_app --port ${API_PORT}`,
      cwd: "../api",
      url: `${API_ORIGIN}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: API_ENV,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: "pnpm run build && pnpm run start",
      url: `${BASE_URL}/dev/components`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
      env: { KT_DEV_COMPONENTS: "1", KT_API_ORIGIN: API_ORIGIN },
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
