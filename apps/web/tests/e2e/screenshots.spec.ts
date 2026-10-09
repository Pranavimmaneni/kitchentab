import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

/**
 * Review screenshots of /dev/components (Chromium project only, see playwright.config.ts).
 * Written to apps/web/test-results/dev-components-{width}-{theme}.png (gitignored).
 */
const OUT_DIR = fileURLToPath(new URL("../../test-results/", import.meta.url));

for (const width of [390, 1280]) {
  for (const theme of ["light", "dark"] as const) {
    test(`screenshot /dev/components at ${width} px in ${theme}`, async ({ browser, baseURL }) => {
      const context = await browser.newContext({
        baseURL,
        viewport: { width, height: 900 },
        deviceScaleFactor: 1,
        colorScheme: theme,
        reducedMotion: "reduce",
      });
      const page = await context.newPage();
      await page.goto("/dev/components");
      await expect(page.getByRole("heading", { level: 1, name: "Components" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({
        path: `${OUT_DIR}dev-components-${width}-${theme}.png`,
        fullPage: true,
      });
      await context.close();
    });
  }
}
