import { expect, test, type Page } from "@playwright/test";
import {
  LINES,
  SHARES,
  WHO,
  createHouse,
  lastShared,
  seedShopRite,
  signInWithEmail,
  stubShare,
  uniqueEmail,
} from "./fixtures/app";

// Journeys run on WebKit iPhone 14 and Chromium Pixel 7 (TEST_PLAN §1).
test.skip(({ browserName }) => browserName === "firefox", "phone journeys: WebKit and Chromium");

const NAMES = { pranav: "You", arjun: "Arjun", meera: "Meera", kabir: "Kabir" } as const;

async function addItem(page: Page, name: string, price: string): Promise<void> {
  await page.getByRole("button", { name: "+ ADD A MISSED ITEM" }).click();
  const sheet = page.getByRole("dialog", { name: "Add a missed item" });
  await sheet.getByLabel("NAME").fill(name);
  await sheet.getByLabel("LINE TOTAL").fill(price);
  await sheet.getByRole("button", { name: "Save" }).click();
  await expect(sheet).toBeHidden();
  await expect(page.getByRole("button", { name: `Edit ${name}` })).toBeVisible();
}

function tag(page: Page, item: string) {
  return page.getByRole("button", { name: new RegExp(`^${item} goes in: `) });
}

test("new payer journey with manual entry (E2E-01 minus the scan step): code sign-in, house of four, invite, ShopRite bill, tags, split, send, share [FR-02] [FR-03] [FR-15] [FR-21] [FR-22] [FR-24] [FR-30] [FR-31]", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  await stubShare(page);
  const email = uniqueEmail("pranav");

  // A → B → C: a brand-new user with no house lands on "Who lives here?".
  await signInWithEmail(page, email);
  await expect(page).toHaveURL(/\/houses\/new$/);
  await expect(page.getByRole("heading", { name: "Who lives here?" })).toBeVisible();
  await expect(page.getByTestId("invite-link")).toContainText("/h/");

  const houseName = page.getByLabel("HOUSE NAME");
  await houseName.fill("Apt 4B");
  for (const name of ["Arjun", "Meera", "Kabir"]) {
    await page.getByRole("textbox", { name: "Add a roommate" }).fill(name);
    await page.getByRole("textbox", { name: "Add a roommate" }).press("Enter");
    await expect(page.getByRole("button", { name: `Remove ${name}` })).toBeVisible();
  }
  // Duplicate names are refused inline.
  await page.getByRole("textbox", { name: "Add a roommate" }).fill("meera");
  await page.getByRole("textbox", { name: "Add a roommate" }).press("Enter");
  await expect(page.getByText("Already in the house")).toBeVisible();
  await page.getByRole("textbox", { name: "Add a roommate" }).fill("");

  // Invite link: Web Share with the link; the link page is noindex + no-referrer (LINK-04).
  await page.getByRole("button", { name: "Drop it in the group chat ↗" }).click();
  const invite = await lastShared(page);
  expect(invite?.url).toMatch(/\/h\/[A-Za-z0-9]+$/);
  const inviteRes = await request.get(new URL(invite!.url!).pathname, { maxRedirects: 0 });
  expect(inviteRes.headers()["referrer-policy"]).toBe("no-referrer");
  expect(inviteRes.headers()["x-robots-tag"]).toBe("noindex");
  // The house name autosaves.
  await expect
    .poll(async () => (await page.request.get("/api/v1/me").then((r) => r.json())).houses[0].name)
    .toBe("Apt 4B");

  // Capture sheet → manual entry → review.
  await page.getByRole("link", { name: "Snap your first receipt →" }).click();
  await page
    .getByRole("dialog", { name: "Snap a receipt" })
    .getByRole("button", { name: "Enter items by hand" })
    .click();
  await page.waitForURL(/\/bills\/[^/]+\/review$/);
  const billUrl = page.url().replace(/\/review$/, "");
  await expect(page.getByRole("heading", { name: "Did we read it right?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Looks good → split it" })).toBeDisabled();
  await expect(page.getByText("Add at least one item first")).toBeVisible();

  await page.getByLabel("STORE").fill("ShopRite");
  await page.getByLabel("DATE").fill("2026-10-02");
  await expect(page.getByLabel("PAID BY")).toHaveValue(/.+/);
  for (const line of LINES) await addItem(page, line.name, (line.cents / 100).toFixed(2));
  await page.getByLabel("TAX").fill("0.89");
  await page.getByLabel("TOTAL", { exact: true }).fill("95.00");
  await expect(page.getByText("*** OFF BY $0.18 ***")).toBeVisible();
  await expect(page.getByRole("button", { name: "Looks good → split it" })).toBeDisabled();
  await page.getByLabel("TOTAL", { exact: true }).fill("95.18");
  await expect(page.getByText("*** MATHS CHECKS OUT ***")).toBeVisible();
  await expect(page.getByText("$94.29")).toBeVisible();

  // Storage tags: new lines start in the pantry; a tap cycles and saves.
  await expect(tag(page, "Whole milk 1 gal")).toHaveText("PANTRY");
  await tag(page, "Whole milk 1 gal").click();
  await expect(tag(page, "Whole milk 1 gal")).toHaveText("NOT FOOD");
  await tag(page, "Whole milk 1 gal").click();
  await expect(tag(page, "Whole milk 1 gal")).toHaveText("FRIDGE");
  await tag(page, "Paper towels 6 pk").click();
  await expect(tag(page, "Paper towels 6 pk")).toHaveText("NOT FOOD");

  await page.getByRole("button", { name: "Looks good → split it" }).click();
  await page.waitForURL(/\/split$/);
  await expect(page.getByRole("heading", { name: "Who's eating what?" })).toBeVisible();
  await expect(page.getByText("STEP 2/3 · SHOPRITE · 10/02")).toBeVisible();
  await expect(page.getByText("12 ITEMS LOOKING FOR A HOME")).toBeVisible();

  // Column header ticks Arjun on everything; Undo puts it back (FR-22).
  await page.getByRole("button", { name: "Tick or untick Arjun on every item" }).click();
  await expect(page.getByText("Arjun is on every item")).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "12");
  await page.getByRole("button", { name: "Undo" }).first().click();
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  await expect(page.getByRole("button", { name: "Arjun uses Whole milk 1 gal" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );

  // Tick the shoprite_sent split, leaving the four everyone-items for "Split the rest" (FR-21).
  const everyone = new Set(["eggs", "rice", "towels", "soap"]);
  for (const line of LINES) {
    if (everyone.has(line.key)) continue;
    for (const who of WHO[line.key]) {
      await page.getByRole("button", { name: `${NAMES[who]} uses ${line.name}` }).click();
    }
  }
  await expect(page.getByText("4 ITEMS LOOKING FOR A HOME")).toBeVisible();
  await page.getByRole("button", { name: "✦ Split the rest with everyone" }).click();
  await expect(page.getByRole("link", { name: "All sorted → review" })).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "12");
  const totals = page.getByRole("group", { name: "Totals so far" });
  for (const amount of Object.values(SHARES)) await expect(totals).toContainText(amount);

  // 4 · Here's the damage: server shares, items per person, coming back to you.
  await page.getByRole("link", { name: "All sorted → review" }).click();
  await page.waitForURL(/\/send$/);
  await expect(page.getByRole("heading", { name: "Here's the damage" })).toBeVisible();
  await expect(page.getByText("10/02/2026 · PAID BY YOU · 12 ITEMS")).toBeVisible();
  for (const amount of Object.values(SHARES))
    await expect(page.getByText(amount, { exact: true })).toBeVisible();
  await expect(page.getByText("ANYONE IN APT 4B CAN HOP ON OR OFF AN ITEM.")).toBeVisible();
  await expect(page.getByText("$71.67")).toBeVisible();
  await page.getByRole("button", { name: "Send it to the house ↗" }).click();

  // E · Share: chat preview, Web Share with the bill link, copy fallback, done.
  await page.waitForURL(/\/share$/);
  await expect(page.getByText("SENT!")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Now tell the house." })).toBeVisible();
  await expect(page.getByText("KITCHENTAB · APT 4B")).toBeVisible();
  await expect(page.getByText("SHOPRITE · $95.18")).toBeVisible();
  await expect(page.getByText("SPLIT 4 WAYS · TAP FOR YOUR SHARE")).toBeVisible();
  await expect(page.getByLabel("Message")).toHaveValue(
    "Groceries are in. Tick anything I got wrong.",
  );
  await page.getByRole("button", { name: "Share ↗" }).click();
  const shared = await lastShared(page);
  expect(shared?.text).toBe("Groceries are in. Tick anything I got wrong.");
  expect(shared?.url).toMatch(/\/b\/[A-Za-z0-9]+$/);
  await page.getByRole("button", { name: "Nudge Meera" }).click();
  expect((await lastShared(page))?.text).toBe("Your share of ShopRite 10/02 is $18.87");
  await expect(page.getByText("HASN'T JOINED YET").first()).toBeVisible();

  await page.getByRole("link", { name: "Done → see the tab" }).click();
  await expect(page).toHaveURL(billUrl);
  await expect(page.getByRole("heading", { name: "WHO OWES WHAT" })).toBeVisible();
});

test("BILL-02 reconciliation blocks splitting until the total matches or is overridden with a logged reason [FR-13]", async ({
  page,
}) => {
  await signInWithEmail(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { billId } = await seedShopRite(page, house);
  await page.goto(`/bills/${billId}/review`);
  await expect(page.getByText("*** MATHS CHECKS OUT ***")).toBeVisible();

  await page.getByLabel("TOTAL", { exact: true }).fill("96.38");
  await expect(page.getByText("*** OFF BY $1.20 ***")).toBeVisible();
  const next = page.getByRole("button", { name: "Looks good → split it" });
  await expect(next).toBeDisabled();
  await expect(page.getByText("The items don't add up to the receipt total yet")).toBeVisible();
  await expect(page.getByRole("button", { name: "Fix items" })).toBeVisible();

  await page.getByRole("button", { name: "Use receipt total anyway" }).click();
  const dialog = page.getByRole("dialog", { name: "Use receipt total anyway" });
  await expect(dialog.getByRole("button", { name: "Use receipt total anyway" })).toBeDisabled();
  await dialog.getByLabel("WHY? THIS GOES IN THE LOG").fill("Bag fee not itemized");
  await dialog.getByRole("button", { name: "Use receipt total anyway" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("*** MATHS CHECKS OUT ***")).toBeVisible();
  await expect(page.getByText("RECEIPT ADJUSTMENT")).toBeVisible();
  await expect(next).toBeEnabled();
  await next.click();
  await page.waitForURL(/\/split$/);
});

test("BILL-03 storage tag cycles FRIDGE → FREEZER → PANTRY → NOT FOOD and every tap saves", async ({
  page,
}) => {
  await signInWithEmail(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { billId } = await seedShopRite(page, house);
  await page.goto(`/bills/${billId}/review`);
  const milk = tag(page, "Whole milk 1 gal");
  await expect(milk).toHaveText("FRIDGE");
  for (const next of ["FREEZER", "PANTRY", "NOT FOOD", "FRIDGE", "FREEZER"]) {
    await milk.click();
    await expect(milk).toHaveText(next);
  }
  await page.reload();
  await expect(tag(page, "Whole milk 1 gal")).toHaveText("FREEZER");
  await expect(page.getByText("TAGS SAY WHERE IT GOES · TAP ONE TO CHANGE IT")).toBeVisible();
});

test("BILL-06 draft autosave every 2 s survives a reload", async ({ page }) => {
  await signInWithEmail(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { billId } = await seedShopRite(page, house);
  await page.goto(`/bills/${billId}/review`);
  await page.getByLabel("STORE").fill("ShopRite Hoboken");
  await page.getByLabel("TAX").fill("1.25");
  await page.getByLabel("TOTAL", { exact: true }).fill("95.54");
  // Saved within one autosave tick, without leaving the field or the page.
  await expect
    .poll(
      async () => {
        const bill = await page.request.get(`/api/v1/bills/${billId}`).then((r) => r.json());
        return [bill.bill.store_name, bill.bill.tax_cents, bill.bill.receipt_total_cents];
      },
      { timeout: 5_000 },
    )
    .toEqual(["ShopRite Hoboken", 125, 9554]);
  await page.reload();
  await expect(page.getByLabel("STORE")).toHaveValue("ShopRite Hoboken");
  await expect(page.getByLabel("TAX")).toHaveValue("1.25");
  await expect(page.getByText("*** MATHS CHECKS OUT ***")).toBeVisible();
});

test("BILL-04 send stays blocked while any item has no one, and says why [FR-24]", async ({
  page,
}) => {
  await signInWithEmail(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { billId } = await seedShopRite(page, house, { split: "all", leave: ["coffee"] });
  await page.goto(`/bills/${billId}/split`);
  await expect(page.getByText("1 ITEM LOOKING FOR A HOME")).toBeVisible();
  await expect(page.getByRole("link", { name: "All sorted → review" })).toHaveCount(0);
  await page.goto(`/bills/${billId}/send`);
  await expect(page.getByRole("button", { name: "Send it to the house ↗" })).toBeDisabled();
  await expect(page.getByText("1 ITEM LOOKING FOR A HOME")).toBeVisible();
});
