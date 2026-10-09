import { defineConfig, devices } from "@playwright/test";

const PORT = 3000;
const BASE_URL = `http://localhost:${PORT}`;

/**
 * E2E projects per TEST_PLAN §1: WebKit (iPhone 14), Chromium (Pixel 7), Firefox desktop.
 * The web server is a production build with the component gallery enabled.
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
  webServer: {
    command: "pnpm run build && pnpm run start",
    url: `${BASE_URL}/dev/components`,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
    env: { KT_DEV_COMPONENTS: "1" },
    stdout: "ignore",
    stderr: "pipe",
  },
});
