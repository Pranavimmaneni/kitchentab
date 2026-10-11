import { expect, test } from "@playwright/test";
import { API_ORIGIN } from "./fixtures/env";
import { call, readCode, uniqueEmail } from "./fixtures/app";
import {
  aptWith,
  closeApt,
  open,
  seedHMart,
  seedShopRite,
  settledTab,
  tile,
} from "./fixtures/household";

/**
 * Milestone 7 journeys against the real API: Tabs (screen 1), Balances, House, Settings and the
 * house switcher (PRD §14A, §5.5). Phones only (WebKit, Chromium).
 */
test.skip(({ browserName }) => browserName === "firefox", "phone journeys: WebKit and Chromium");

test("BAL-01 MONEY-08 Tabs shows +$40.37 with ARJUN → YOU $33.91, MEERA → YOU $8.14, YOU → KABIR $1.68 and Balances breaks each pair down by tab [FR-50] [FR-51] [FR-52]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page);
  const shoprite = await seedShopRite(page, apt, { date: "2026-10-02", meeraOnCoffee: true });
  await call(page, "POST", `/bills/${shoprite.billId}/payments`, {
    amount_cents: 1889,
    method: "cash",
    participant: `m:${apt.member.kabir}`,
  });
  await seedHMart(page, apt);

  await open(page, "/tabs");
  await expect(page.getByRole("heading", { level: 1, name: "Hey, Pranav." })).toBeVisible();
  const card = page.getByRole("region", { name: "BALANCE" });
  await expect(card.getByText("+$40.37")).toBeVisible();
  await expect(card.getByText("coming to you")).toBeVisible();
  const lines = card.locator(".kt-ticker__line");
  await expect(lines).toHaveText([
    /ARJUN\s*→\s*to\s*YOU\$33\.91/,
    /MEERA\s*→\s*to\s*YOU\$8\.14/,
    /YOU\s*→\s*to\s*KABIR\$1\.68/,
  ]);
  await expect(lines.nth(2)).toHaveAttribute("data-kind", "owing");
  await expect(card.getByText("NETTED ACROSS EVERY OPEN TAB")).toBeVisible();

  const open_ = page.getByRole("region", { name: /OPEN TABS/ });
  await expect(open_.getByRole("heading")).toHaveText(/OPEN TABS\s*2/);
  const shopriteCard = open_.getByRole("link", { name: /ShopRite/ });
  await expect(shopriteCard).toContainText("Oct 2 · you paid · 12 items");
  await expect(shopriteCard).toContainText("$95.18");
  await expect(shopriteCard).toContainText("1/3 PAID");
  const hmart = open_.getByRole("link", { name: /H Mart/ });
  await expect(hmart).toContainText("Meera paid · 9 items");
  await expect(hmart).toContainText("YOU OWE $14.10");

  // Search by item name (FR-53).
  await page.getByLabel("Search tabs").fill("dumpl");
  await expect(page.getByText("1 TAB FOUND")).toBeVisible();

  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "BALANCES" })
    .click();
  await page.waitForURL("**/balances");
  await expect(page.getByText("+$40.37")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Arjun owes you\s*\$33\.91/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^You owe Kabir\s*\$1\.68/ })).toBeVisible();
  const meera = page.getByRole("button", { name: /^Meera owes you\s*\$8\.14/ });
  await meera.click();
  await expect(meera).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("link", { name: /ShopRite · Oct 2\s*\+\$22\.24/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /H Mart · .*\s*−\$14\.10/ })).toBeVisible();
  await expect(page.getByRole("link", { name: "Pay" })).toHaveAttribute(
    "href",
    `/bills/${shoprite.billId}`,
  );
  await closeApt(apt);
});

test("BAL-04 a new house reads ALL SQUARE with the empty state, and the Kitchen pill agrees [FR-50]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(90_000);
  const apt = await aptWith(browser, baseURL, page);
  await open(page, "/tabs");
  await expect(page.getByRole("region", { name: "BALANCE" }).getByText("ALL SQUARE")).toBeVisible();
  await expect(page.getByRole("heading", { name: "No tabs yet." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Snap your first receipt" })).toHaveAttribute(
    "href",
    `/bills/new?house=${apt.id}`,
  );
  await open(page, "/");
  await expect(page.getByRole("link", { name: "ALL SQUARE" })).toHaveAttribute("href", "/tabs");
  await closeApt(apt);
});

test("VIS-03 settled history shows a few tabs, then pages 20 at a time on the page itself, never an inner scroll [FR-52]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(240_000);
  // Three payers share the seeding so nobody hits the 60 writes a minute limit.
  const apt = await aptWith(browser, baseURL, page, ["arjun", "meera"]);
  const payers = [
    { page, id: apt.member.pranav },
    { page: apt.pages.arjun!, id: apt.member.arjun },
    { page: apt.pages.meera!, id: apt.member.meera },
  ];
  await Promise.all(
    payers.map(async (payer, p) => {
      for (let i = p + 1; i <= 23; i += payers.length) {
        const n = String(i).padStart(2, "0");
        await settledTab(payer.page, apt, payer.id, `Corner store ${n}`, `2026-08-${n}`);
      }
    }),
  );
  await open(page, "/tabs");
  const list = page.getByTestId("settled-list");
  await expect(list.getByRole("link")).toHaveCount(3);
  await expect(list.getByRole("link").first()).toContainText("Corner store 23");
  await expect(list.getByRole("link").first()).toContainText("SETTLED");
  await page.getByRole("button", { name: "Show all history" }).click();
  await expect(list.getByRole("link")).toHaveCount(20);
  await page.getByRole("button", { name: "Show 20 more" }).click();
  await expect(list.getByRole("link")).toHaveCount(23);
  await expect(list.getByRole("link").last()).toContainText("Corner store 01");
  await expect(page.getByRole("button", { name: "Show 20 more" })).toHaveCount(0);
  const inner = await page.evaluate(
    () =>
      [...document.querySelectorAll("main *")].filter((el) => {
        const s = getComputedStyle(el);
        return /(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 1;
      }).length,
  );
  expect(inner).toBe(0);
  await closeApt(apt);
});

test("HOUSE-06 HOUSE-08 FR-04 the admin renames the house, rotates the invite link, removes a member and hands admin to a joined roommate [FR-04] [FR-06] [FR-07]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun"]);
  const { arjun } = apt.pages as Required<typeof apt.pages>;
  await open(page, "/house");
  await expect(page.getByText("HOUSE · 4 MEMBERS")).toBeVisible();

  await page.getByLabel("HOUSE NAME").fill("Apt 4C");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Apt 4C" })).toBeVisible();

  const oldToken = apt.inviteToken;
  await expect(page.getByTestId("invite-link")).toContainText(`/h/${oldToken}`);
  await page.getByRole("button", { name: "Rotate link" }).click();
  await page
    .getByRole("dialog", { name: "Rotate the invite link?" })
    .getByRole("button", { name: "Rotate link" })
    .click();
  await expect(page.getByTestId("invite-link")).not.toContainText(oldToken);
  const stale = await page.request.get(`${API_ORIGIN}/api/v1/join/${oldToken}`);
  expect(stale.status()).toBe(410);

  await page.getByRole("button", { name: "Options for Kabir" }).click();
  await page
    .getByRole("dialog", { name: "Kabir" })
    .getByRole("button", { name: "Remove from house" })
    .click();
  await page
    .getByRole("dialog", { name: "Remove Kabir?" })
    .getByRole("button", { name: "Remove" })
    .click();
  await expect(page.locator(".kt-member-row", { hasText: "Kabir" })).toHaveCount(0);

  // A placeholder can't be admin: only joined members get "Make admin".
  await page.getByRole("button", { name: "Options for Meera" }).click();
  // Wait for the sheet itself: an absence check passes before it has even opened, and an Escape
  // sent then is lost, leaving the sheet over the next click.
  const meera = page.getByRole("dialog", { name: "Meera" });
  await expect(meera.getByRole("button", { name: "Remove from house" })).toBeVisible();
  await expect(meera.getByRole("button", { name: "Make admin" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(meera).toBeHidden();

  await page.getByRole("button", { name: "Options for Arjun" }).click();
  await page
    .getByRole("dialog", { name: "Arjun" })
    .getByRole("button", { name: "Make admin" })
    .click();
  await page
    .getByRole("dialog", { name: "Make Arjun admin?" })
    .getByRole("button", { name: "Make admin" })
    .click();
  await expect(
    page.locator(".kt-member-row", { hasText: "Arjun" }).getByText("ADMIN"),
  ).toBeVisible();
  // Exactly one admin: Pranav lost the admin controls.
  await expect(page.getByRole("button", { name: /^Options for / })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Rotate link" })).toHaveCount(0);
  await expect(page.getByLabel("HOUSE NAME")).toHaveCount(0);

  await open(arjun, "/house");
  await expect(arjun.getByRole("button", { name: "Rotate link" })).toBeVisible();
  await expect(arjun.getByRole("button", { name: "Options for Pranav" })).toBeVisible();
  await closeApt(apt);
});

test("HOUSE-07 EDGE-15-23 leaving is blocked while you paid for open tabs, with the reason; after settling you can leave [FR-06]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun"]);
  const { arjun } = apt.pages as Required<typeof apt.pages>;
  // Arjun pays for a tab, so he can't leave while it's open.
  const bill = await call<{ id: string }>(arjun, "POST", `/houses/${apt.id}/bills`, {
    store_name: "Bodega",
    purchase_date: "2026-10-01",
  });
  const item = await call<{ id: string }>(arjun, "POST", `/bills/${bill.id}/items`, {
    name: "Snacks",
    line_total_cents: 600,
    storage: "pantry",
  });
  await call(arjun, "PUT", `/bills/${bill.id}/allocations`, {
    changes: [{ item_id: item.id, participant: `m:${apt.member.pranav}`, on: true }],
  });
  await call(arjun, "POST", `/bills/${bill.id}/send`);

  await open(arjun, "/house");
  await arjun.getByRole("button", { name: "Leave house" }).click();
  const dialog = arjun.getByRole("dialog", { name: "Leave Apt 4B?" });
  await dialog.getByRole("button", { name: "Leave" }).click();
  await expect(dialog.getByRole("alert")).toHaveText(
    "You paid for open tabs. Settle them or move the payer before you leave.",
  );
  await dialog.getByRole("button", { name: "Not yet" }).click();

  await call(arjun, "POST", `/bills/${bill.id}/settle`, { confirm_outstanding: true });
  await arjun.getByRole("button", { name: "Leave house" }).click();
  await arjun
    .getByRole("dialog", { name: "Leave Apt 4B?" })
    .getByRole("button", { name: "Leave" })
    .click();
  await arjun.waitForURL((url) => url.pathname === "/houses/new", { timeout: 20_000 });
  await open(page, "/house");
  await expect(page.locator(".kt-member-row", { hasText: "Arjun" })).toContainText("LEFT");
  await closeApt(apt);
});

test("HOUSE-09 with two houses the switcher sheet changes house, and the last-used one opens next time [FR-08]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(120_000);
  const apt = await aptWith(browser, baseURL, page);
  await seedShopRite(page, apt);
  await call(page, "POST", "/houses", {
    name: "Beach house",
    roommates: [],
    timezone: "America/New_York",
  });
  await open(page, "/");
  // The newest house opens first.
  await expect(page.getByRole("button", { name: /^KITCHENTAB \*\* BEACH HOUSE/ })).toBeVisible();
  await page.getByRole("button", { name: /Switch house/ }).click();
  const sheet = page.getByRole("dialog", { name: "Your houses" });
  await expect(sheet.getByRole("button", { name: /Beach house/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await sheet.getByRole("button", { name: /Apt 4B/ }).click();
  await expect(page.getByRole("button", { name: /^KITCHENTAB \*\* APT 4B/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Open the fridge: 6 items/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: /^KITCHENTAB \*\* APT 4B/ })).toBeVisible();
  await open(page, "/tabs");
  await expect(page.getByRole("link", { name: /ShopRite/ })).toBeVisible();
  await closeApt(apt);
});

test("AUTH-12 KITCH-09 Settings: the last sign-in method can't be removed until another is added; the sharing default opens new items [FR-01] [FR-70]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page);
  await open(page, "/settings");
  const methods = page.locator(".kt-method-row");
  await expect(methods).toHaveCount(1);
  await expect(methods.getByRole("button", { name: /^Remove / })).toBeDisabled();
  await expect(
    page.getByText("Add another way to sign in before removing this one."),
  ).toBeVisible();

  const second = uniqueEmail("pranav.alt");
  await page.getByRole("button", { name: "Add an email" }).click();
  await page.getByLabel("YOUR EMAIL").fill(second);
  await page.getByRole("button", { name: "Send me a code →" }).click();
  await expect(page.getByText(/We sent a 6-digit code to/)).toBeVisible();
  await page.getByLabel("6-digit code").fill(await readCode(page.request, second));
  await expect(methods).toHaveCount(2);
  await methods
    .first()
    .getByRole("button", { name: /^Remove / })
    .click();
  await expect(methods).toHaveCount(1);
  await expect(methods.getByRole("button", { name: /^Remove / })).toBeDisabled();

  // Payment handles are a set-state write.
  await page.getByLabel("VENMO @").fill("pranav-r");
  await page.getByRole("button", { name: "Save handles" }).click();
  await expect(page.getByRole("status").getByText("Payment handles saved")).toBeVisible();
  expect((await call<{ venmo: string }>(page, "GET", "/me/payment-handles")).venmo).toBe(
    "pranav-r",
  );

  // FR-70: items stocked after switching the default on start open for me.
  const sw = page.getByRole("switch", { name: "Open to share new items by default" });
  await sw.click();
  await expect(sw).toHaveAttribute("aria-checked", "true");
  await expect
    .poll(
      async () =>
        (await call<{ user: { share_by_default: boolean } }>(page, "GET", "/me")).user
          .share_by_default,
    )
    .toBe(true);
  await seedShopRite(page, apt);
  await open(page, "/kitchen/fridge");
  await tile(page, "Chicken thighs 3 lb").click();
  await expect(page.getByRole("switch", { name: "I'm open to sharing" })).toHaveAttribute(
    "aria-checked",
    "true",
  );

  // Appearance follows the segmented control and is saved to the account.
  await page.keyboard.press("Escape");
  await open(page, "/settings");
  await page.getByRole("tab", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect
    .poll(async () => (await call<{ user: { theme: string } }>(page, "GET", "/me")).user.theme)
    .toBe("dark");
  await closeApt(apt);
});

test("AUTH-14 EDGE-15-22 deleting an account needs DELETE typed, signs out, and the name becomes Former roommate for the others", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  await open(kabir, "/settings");
  await kabir.getByRole("button", { name: "Delete account" }).click();
  const dialog = kabir.getByRole("dialog", { name: "Delete your account?" });
  await expect(dialog.getByText(/Former roommate/)).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "Delete account" });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel("TYPE DELETE TO CONFIRM").fill("delete");
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel("TYPE DELETE TO CONFIRM").fill("DELETE");
  await confirm.click();
  await kabir.waitForURL("**/start", { timeout: 20_000 });

  await open(page, "/house");
  await expect(page.locator(".kt-member-row", { hasText: "Former roommate" })).toBeVisible();
  await expect(page.locator(".kt-member-row", { hasText: "Kabir" })).toHaveCount(0);
  await closeApt(apt);
});

test("SET-01 SET-02 SET-03 Settings: a new name reaches the house, notifications sit in one group, home screen steps lead with this phone, and Leave house works from here [FR-83] [FR-84]", async ({
  page,
  browser,
  baseURL,
  browserName,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  await open(kabir, "/settings");

  // FR-83: Save name renames Kabir in the house too.
  await kabir.getByLabel("YOUR NAME").fill("Kabir S");
  await kabir.getByRole("button", { name: "Save", exact: true }).click();
  await expect(kabir.getByRole("status").getByText("Saved")).toBeVisible();
  await open(page, "/house");
  await expect(page.locator(".kt-member-row", { hasText: "Kabir S" })).toBeVisible();

  // FR-84: notifications in one group with the N2 link and the email switch.
  const group = kabir.getByRole("region", { name: "NOTIFICATIONS" });
  await expect(group.getByRole("link", { name: /Notifications/ })).toHaveAttribute(
    "href",
    "/settings/notifications",
  );
  await expect(
    group.getByRole("switch", { name: "Email me about new tabs and changes" }),
  ).toBeVisible();

  // Home screen steps: this phone's first. The payer's page is the project's device (WebKit runs as
  // an iPhone 14, Chromium as a Pixel 7); roommates get other profiles from the fixture.
  await open(page, "/settings");
  const home = page.getByTestId("home-screen");
  await expect(
    home.getByRole("heading", { name: "ADD KITCHENTAB TO YOUR HOME SCREEN" }),
  ).toBeVisible();
  const first = home.locator("[data-testid^=install-]").first();
  await expect(first).toHaveAttribute(
    "data-testid",
    browserName === "webkit" ? "install-iphone" : "install-android",
  );
  await expect(home.getByText("Tap Share, the square with the arrow pointing up.")).toBeVisible();

  // Leave house from Settings.
  await kabir.getByRole("button", { name: "Leave Apt 4B" }).click();
  await kabir
    .getByRole("dialog", { name: "Leave Apt 4B?" })
    .getByRole("button", { name: "Leave" })
    .click();
  await kabir.waitForURL((url) => !url.pathname.startsWith("/settings"), { timeout: 20_000 });
  await open(page, "/house");
  await expect(page.locator(".kt-member-row", { hasText: "Kabir S" })).toContainText("LEFT");

  // Opened from the home screen, the install section is gone.
  await page.addInitScript(() => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = (query: string) =>
      query.includes("display-mode: standalone")
        ? ({ ...real(query), matches: true, media: query } as MediaQueryList)
        : real(query);
  });
  await open(page, "/settings");
  await expect(page.getByRole("heading", { name: "NOTIFICATIONS" })).toBeVisible();
  await expect(page.getByTestId("home-screen")).toHaveCount(0);
  await closeApt(apt);
});
