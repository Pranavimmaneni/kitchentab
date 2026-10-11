import { expect, test, type Page } from "@playwright/test";
import { call, seedShopRite } from "./fixtures/app";
import { API_ORIGIN, TEST_INBOX_SECRET } from "./fixtures/env";
import { aptWith, closeApt } from "./fixtures/household";

/**
 * EMAIL-05: the notification email (apps/api app/services/notify.py `render_email`) read from the
 * test inbox renders legibly in a light and a dark mail client. Screenshots go to the test output.
 */

test.skip(({ browserName }) => browserName !== "chromium", "email rendering: one engine is enough");

type Mail = { to: string; subject: string; html: string | null; text: string };

/** WCAG contrast of two `rgb(...)` colours. */
function contrast(a: string, b: string): number {
  const lum = (c: string) => {
    const [r, g, bl] = (c.match(/\d+(\.\d+)?/g) ?? ["0", "0", "0"])
      .slice(0, 3)
      .map(Number)
      .map((v) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
    return 0.2126 * r! + 0.7152 * g! + 0.0722 * bl!;
  };
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x! + 0.05) / (y! + 0.05);
}

async function colours(page: Page, selector: string): Promise<{ fg: string; bg: string }> {
  return page
    .locator(selector)
    .first()
    .evaluate((el) => {
      let node: Element | null = el;
      let bg = "rgba(0, 0, 0, 0)";
      while (node && (bg === "rgba(0, 0, 0, 0)" || bg === "transparent")) {
        bg = getComputedStyle(node).backgroundColor;
        node = node.parentElement;
      }
      return { fg: getComputedStyle(el).color, bg };
    });
}

test("EMAIL-05 the new-tab email renders legibly in light and dark mail clients [FR-42]", async ({
  page,
  browser,
  baseURL,
}, info) => {
  test.setTimeout(120_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun"]);
  try {
    const arjun = apt.pages.arjun!;
    // Quiet hours (default on, 22:00-08:00 in the house's time zone) hold email overnight, and
    // this suite runs at any hour (CI at 2 AM New York time). The holding itself is PUSH-09.
    await call(arjun, "PUT", "/me/notification-prefs", { quiet_hours: false });
    await seedShopRite(page, apt, { split: "all", send: true });
    const email = (await call<{ user: { email: string } }>(arjun, "GET", "/me")).user.email;
    let mail: Mail | undefined;
    await expect(async () => {
      await page.request.post(`${API_ORIGIN}/__test/drain`);
      const res = await page.request.get(
        `${API_ORIGIN}/api/test/inbox?to=${encodeURIComponent(email)}&limit=20`,
        {
          headers: { "X-Test-Secret": TEST_INBOX_SECRET },
        },
      );
      const list = (await res.json()) as Mail[];
      mail = list.find((m) => m.html && /ShopRite/.test(m.subject));
      expect(mail).toBeTruthy();
    }).toPass({ timeout: 30_000, intervals: [500, 1000, 2000] });

    const html = mail!.html!;
    expect(html).toContain('name="color-scheme" content="light dark"');
    for (const scheme of ["light", "dark"] as const) {
      const view = await browser.newPage({
        colorScheme: scheme,
        viewport: { width: 600, height: 700 },
      });
      await view.setContent(html);
      await view.screenshot({ path: info.outputPath(`email-${scheme}.png`), fullPage: true });
      await expect(view.locator("h1")).toHaveText(mail!.subject);
      for (const sel of ["h1", ".card p", ".foot", "a.btn"]) {
        const { fg, bg } = await colours(view, sel);
        expect(contrast(fg, bg), `${scheme} ${sel}: ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
      const body = await view.evaluate(() => getComputedStyle(document.body).backgroundColor);
      expect(body).toBe(scheme === "dark" ? "rgb(13, 13, 11)" : "rgb(236, 234, 226)");
      await expect(view.getByRole("link", { name: "Open KitchenTab" })).toBeVisible();
      await expect(view.getByRole("link", { name: "Stop these emails" })).toBeVisible();
      await view.close();
    }
  } finally {
    await closeApt(apt);
  }
});
