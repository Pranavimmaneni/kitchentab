import { expect, test, type Page } from "@playwright/test";
import { call, uniqueEmail } from "./fixtures/app";
import { apiSignIn, aptWith, closeApt, open } from "./fixtures/household";
import { quietPrompts } from "./fixtures/quiet";
import { profileFor } from "./fixtures/roommates";
import { isStream } from "./fixtures/sync";

/**
 * AUTH-09 / E2E-09: one person on a phone (two tabs) and a laptop, plus a roommate. "Sign out
 * everywhere" on one tab revokes every session of that person: the other tab hears it over the
 * BroadcastChannel, the laptop through its house stream (`revoked {reason: "signed_out"}`), and
 * every one of them lands on sign-in within 2 s. The roommate stays signed in.
 */
test("AUTH-09 E2E-09 sign out everywhere sends every tab and device of that person to sign-in within 2 s; a roommate stays signed in [FR-01] @multi", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(150_000);
  const apt = await aptWith(browser, baseURL, page, ["kabir"]);
  const kabir = apt.pages.kabir!;
  const email = uniqueEmail("pranav");
  // `aptWith` signed Pranav in with its own address; this person signs in on every device with one.
  const laptopContext = await browser.newContext({ ...profileFor(browser, 1).options, baseURL });
  apt.contexts.push(laptopContext);
  const phoneContext = await browser.newContext({ ...profileFor(browser, 0).options, baseURL });
  apt.contexts.push(phoneContext);
  // Two tabs make a second visit; the install card is not what this test is about.
  await quietPrompts(phoneContext);
  await quietPrompts(laptopContext);
  const phone = await phoneContext.newPage();
  await apiSignIn(phone, email);
  const laptop = await laptopContext.newPage();
  await apiSignIn(laptop, email);
  // That person is Meera in Apt 4B.
  await call(phone, "POST", `/join/${apt.inviteToken}/claim`, { member_id: apt.member.meera });

  const settings = phone;
  const secondTab = await phoneContext.newPage();
  const opened = async (target: Page, path: string) => {
    const stream = target.waitForRequest(isStream, { timeout: 30_000 }).catch(() => null);
    await open(target, path);
    return stream;
  };
  // The phone's two tabs share one stream (leader + relay); the laptop has its own.
  await Promise.all([
    opened(secondTab, "/tabs"),
    opened(laptop, "/balances"),
    opened(kabir, "/tabs"),
  ]);
  await open(settings, "/settings");
  await expect(laptop.getByRole("heading", { level: 1 })).toBeVisible();

  await settings.getByRole("button", { name: "Sign out everywhere" }).click();
  await Promise.all(
    [settings, secondTab, laptop].map((target) =>
      expect(target, "every tab of that person is on sign-in").toHaveURL(/\/start$/, {
        timeout: 2000,
      }),
    ),
  );
  await expect(laptop.getByRole("button", { name: "Send me a code →" })).toBeVisible();
  // The session really is gone, not just the screen.
  expect((await laptop.request.get("/api/v1/me")).status()).toBe(401);
  expect((await secondTab.request.get("/api/v1/me")).status()).toBe(401);

  // Kabir is another person: still signed in and live.
  await kabir.waitForTimeout(500);
  await expect(kabir).toHaveURL(/\/tabs$/);
  expect((await kabir.request.get("/api/v1/me")).status()).toBe(200);
  await closeApt(apt);
});
