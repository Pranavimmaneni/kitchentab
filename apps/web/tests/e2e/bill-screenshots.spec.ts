import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { call } from "./fixtures/app";
import { expect, test } from "./fixtures/roommates";
import { apartment, openBill } from "./fixtures/sync";

/**
 * Review screenshots of screen 5 (open bill, Bill.dc.html) at 390 px in light and dark (Chromium
 * only, via the `screenshots.spec.ts` ignore rule). Written to
 * apps/web/test-results/screens/5-*.png for comparison with docs/design/renders.
 */
const OUT = fileURLToPath(new URL("../../test-results/screens/", import.meta.url));

async function shoot(page: Page, name: string): Promise<void> {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.evaluate(() => document.fonts.ready);
    const height = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.setViewportSize({ width: 390, height: Math.max(844, height) });
    await page.screenshot({ path: `${OUT}${name}-${theme}.png` });
    await page.setViewportSize({ width: 390, height: 844 });
  }
  await page.emulateMedia({ colorScheme: "light" });
}

test("VIS-01 screen 5 open bill at 390 px in both themes: payer, member, items view, settled [FR-36] [FR-39] [FR-43]", async ({
  roommates,
}) => {
  test.setTimeout(180_000);
  const house = await roommates(3);
  const apt = await apartment(house, ["arjun", "kabir"]);
  const [payer, arjun, kabir] = [apt.as.pranav!, apt.as.arjun!, apt.as.kabir!];
  // Kabir paid and is waiting on Pranav; Meera hopped on the coffee.
  await call(kabir, "POST", `/bills/${apt.billId}/payments`, { method: "venmo" });
  await call(payer, "PUT", `/bills/${apt.billId}/allocations`, {
    changes: [{ item_id: apt.itemIds.coffee, participant: `m:${apt.member.meera}`, on: true }],
  });
  for (const page of [payer, arjun]) await page.setViewportSize({ width: 390, height: 844 });
  await Promise.all([openBill(payer, apt.billId), openBill(arjun, apt.billId)]);
  await expect(payer.getByRole("button", { name: "Confirm", exact: true })).toBeVisible();
  await shoot(payer, "5-bill-payer");
  await expect(arjun.getByRole("button", { name: "Pay $33.91" })).toBeVisible();
  await shoot(arjun, "5-bill-member");
  await arjun.getByRole("tab", { name: "Items & splits" }).click();
  await expect(arjun.getByRole("grid", { name: "Who's eating what" })).toBeVisible();
  await shoot(arjun, "5-bill-items");
  await call(payer, "POST", `/bills/${apt.billId}/settle`, { confirm_outstanding: true });
  await expect(payer.getByText(/^SETTLED \d\d\/\d\d$/)).toBeVisible({ timeout: 2000 });
  await shoot(payer, "5-bill-settled");
});
