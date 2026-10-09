import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const PATH = "/dev/components";

async function open(page: Page): Promise<void> {
  await page.goto(PATH);
  await expect(page.getByRole("heading", { level: 1, name: "Components" })).toBeVisible();
  // Both theme panels are on the page.
  await expect(page.getByRole("heading", { level: 2, name: "Light theme" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Dark theme" })).toBeVisible();
}

test.describe("/dev/components gallery", () => {
  for (const scheme of ["light", "dark"] as const) {
    test(`axe reports no serious or critical violations with the page in ${scheme}`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"])
        .analyze();
      const blocking = results.violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => ({
          id: v.id,
          impact: v.impact,
          targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
        }));
      expect(blocking).toEqual([]);
    });
  }

  test("axe stays clean with the bottom sheet open", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Open sheet" }).first().click();
    await expect(page.getByRole("dialog", { name: /Pay Pranav/ })).toBeVisible();
    const results = await new AxeBuilder({ page }).include(".kt-sheet").analyze();
    const blocking = results.violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(blocking.map((v) => v.id)).toEqual([]);
  });

  test("every button, link and tab is at least 44 × 44 px", async ({ page }) => {
    await open(page);
    const measure = () =>
      page.evaluate(() => {
        const small: string[] = [];
        const nodes = document.querySelectorAll<HTMLElement>(
          'button, a[href], [role="tab"], [role="button"]',
        );
        nodes.forEach((el) => {
          const style = getComputedStyle(el);
          if (style.display === "none" || style.visibility === "hidden") return;
          // Untransformed layout size: rotation must not inflate or shrink the measurement.
          const width = el.offsetWidth;
          const height = el.offsetHeight;
          if (width === 0 && height === 0) return;
          if (width < 44 || height < 44) {
            const name =
              el.getAttribute("aria-label") ?? el.textContent?.trim().slice(0, 40) ?? el.tagName;
            small.push(`${el.tagName.toLowerCase()} "${name}" ${width}×${height}`);
          }
        });
        return { checked: nodes.length, small };
      });
    const base = await measure();
    expect(base.checked).toBeGreaterThan(100);
    expect(base.small).toEqual([]);

    await page.getByRole("button", { name: "Open sheet" }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    expect((await measure()).small).toEqual([]);
  });

  test("has no horizontal page scroll at 390 px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page);
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      bodyScrollWidth: document.body.scrollWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
    expect(overflow.bodyScrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
  });

  test("Space toggles a split checkbox once and arrow keys move between boxes", async ({
    page,
  }) => {
    await open(page);
    const grid = page.getByRole("grid", { name: "Who's eating what" }).first();
    const yogurt = grid.getByRole("button", { name: "Meera uses Greek yogurt" });
    await expect(yogurt).toHaveAttribute("aria-pressed", "true");
    await yogurt.focus();
    await page.keyboard.press("Space");
    await expect(yogurt).toHaveAttribute("aria-pressed", "false");
    await page.keyboard.press("Space");
    await expect(yogurt).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("ArrowRight");
    await expect(grid.getByRole("button", { name: "Kabir uses Greek yogurt" })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(grid.getByRole("button", { name: "Kabir uses Bananas" })).toBeFocused();
  });

  test("the bottom sheet closes on Escape and returns focus to its opener", async ({ page }) => {
    await open(page);
    const opener = page.getByRole("button", { name: "Open sheet" }).first();
    await opener.click();
    const sheet = page.getByRole("dialog", { name: /Pay Pranav/ });
    await expect(sheet).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    await expect(opener).toBeFocused();
  });

  test("the theme toggle flips <html data-theme> and survives a reload", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await open(page);
    const toggle = page
      .locator("header")
      .first()
      .getByRole("button", { name: "Switch to dark mode" });
    await toggle.click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(
      page.locator("header").first().getByRole("button", { name: "Switch to light mode" }),
    ).toBeVisible();
  });
});

test.describe("reduced motion", () => {
  test("nothing animates when the user prefers reduced motion", async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL, reducedMotion: "reduce" });
    const page = await context.newPage();
    await open(page);
    // Exercise every animated component: stamp a checkbox, open and close the sheet, show a toast.
    const box = page.getByRole("button", { name: "Meera uses Eggs, 18 ct" }).first();
    await box.click();
    await box.click();
    await page.getByRole("button", { name: "Open sheet" }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const whileOpen = await page.evaluate(() => document.getAnimations().length);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
    await page.getByRole("button", { name: "Show toast" }).first().click();
    const after = await page.evaluate(() =>
      document.getAnimations().map((a) => (a as CSSAnimation).animationName ?? a.constructor.name),
    );
    expect(whileOpen).toBe(0);
    expect(after).toEqual([]);
    await context.close();
  });

  test("control: the same page does animate when motion is allowed", async ({
    browser,
    baseURL,
  }) => {
    const context = await browser.newContext({ baseURL, reducedMotion: "no-preference" });
    const page = await context.newPage();
    await open(page);
    // Skeleton shimmer runs forever when motion is allowed, so the check above can see animations.
    const running = await page.evaluate(() => document.getAnimations().length);
    expect(running).toBeGreaterThan(0);
    await context.close();
  });
});
