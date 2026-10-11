import { expect, test, type Page } from "@playwright/test";
import { call, createHouse, hydrated, seedShopRite, uniqueEmail } from "./fixtures/app";
import { API_ORIGIN } from "./fixtures/env";
import { apiSignIn } from "./fixtures/household";
import { profileFor } from "./fixtures/roommates";

/**
 * E2E-07 (TEST_PLAN §4): Chromium installs KitchenTab from the browser's prompt, turns on
 * notifications from Settings, and then receives a push for each kind of thing a roommate can do
 * (a share change lands in the inbox at once; its push waits out the 10-minute coalescing window).
 * Pushes really travel: the API encrypts each one (RFC 8291) for a subscription minted by the fake
 * push service (infra/docker-compose.yml), which decrypts and records it. Headless Chromium can't
 * reach a real push service or show a native install prompt, so `beforeinstallprompt` is fired
 * by hand and PushManager hands out the fake service's subscription.
 */

test.skip(({ browserName }) => browserName !== "chromium", "install prompt and push: Chromium");
// The full build: the headless shell always reports notification permission as denied.
test.use({ channel: "chromium", serviceWorkers: "allow" });

const FAKE_PUSH = process.env.KT_E2E_FAKE_PUSH ?? "http://localhost:4010";

type Sub = { endpoint: string; keys: { p256dh: string; auth: string } };

async function firePrompt(page: Page): Promise<void> {
  await page.evaluate(() => {
    const event = new Event("beforeinstallprompt", { cancelable: true }) as Event & {
      prompt: () => Promise<void>;
      userChoice: Promise<{ outcome: string }>;
    };
    event.prompt = async () => {
      localStorage.setItem("stub.prompted", "1");
    };
    event.userChoice = Promise.resolve({ outcome: "accepted" });
    window.dispatchEvent(event);
  });
}

test("E2E-07 Chromium installs from the prompt, turns on notifications in Settings, and a push arrives for each thing a roommate does [FR-42] [FR-43]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(300_000);
  const context = page.context();
  // This device's push subscription comes from the fake push service (real keys, real endpoint).
  const device = `pranav-chromium-${Date.now().toString(36)}`;
  const sub = (await (
    await page.request.post(`${FAKE_PUSH}/subscriptions`, { data: { user: device } })
  ).json()) as Sub;
  await context.addInitScript((s: Sub) => {
    const make = () => ({
      endpoint: s.endpoint,
      expirationTime: null,
      options: { applicationServerKey: null, userVisibleOnly: true },
      toJSON: () => ({ endpoint: s.endpoint, keys: s.keys }),
      unsubscribe: async () => {
        localStorage.removeItem("stub.subscribed");
        return true;
      },
      getKey: () => null,
    });
    PushManager.prototype.getSubscription = async function () {
      return (localStorage.getItem("stub.subscribed")
        ? make()
        : null) as unknown as PushSubscription;
    };
    PushManager.prototype.subscribe = async function () {
      localStorage.setItem("stub.subscribed", "1");
      return make() as unknown as PushSubscription;
    };
  }, sub);

  await apiSignIn(page, uniqueEmail("pranav"));
  const apt = await createHouse(page);
  // Pushes at any hour: quiet hours (on by default) would hold them overnight.
  await call(page, "PUT", "/me/notification-prefs", { quiet_hours: false });

  // Install: the prompt is kept on the first visit and offered on the second (PWA-05).
  await page.goto("/");
  await hydrated(page);
  await firePrompt(page);
  const second = await context.newPage();
  await second.goto("/tabs");
  await hydrated(second);
  await firePrompt(second);
  const card = second.getByRole("region", { name: "Add KitchenTab to your home screen" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Install" }).click();
  await expect(card).toBeHidden();
  expect(await second.evaluate(() => localStorage.getItem("stub.prompted"))).toBe("1");
  await second.close();

  // Notifications: Settings › Notifications › Turn on → N1 → Turn on notifications.
  await page.goto("/settings/notifications");
  await hydrated(page);
  await expect(page.getByText("PUSH IS OFF")).toBeVisible();
  // The browser's own permission prompt: the person says Allow when the app asks (after the tap).
  await context.grantPermissions(["notifications"]);
  await page.getByRole("button", { name: "Turn on", exact: true }).click();
  const n1 = page.getByRole("dialog", { name: "Don't miss an ask." });
  await n1.getByRole("button", { name: "Turn on notifications" }).click();
  await expect(page.getByText("Notifications are on.")).toBeVisible();
  await expect
    .poll(async () => (await call<unknown[]>(page, "GET", "/push/subscriptions")).length)
    .toBe(1);
  // Settled tabs don't push by default; switch them on like a person would.
  const settledPush = page.getByRole("button", { name: "Push for tabs settled or reopened" });
  await settledPush.click();
  await expect(settledPush).toHaveAttribute("aria-pressed", "true");

  // Roommates act; Pranav does nothing but receive.
  async function roommate(who: "arjun" | "kabir"): Promise<Page> {
    const ctx = await browser.newContext({
      ...profileFor(browser, who === "arjun" ? 1 : 2).options,
      baseURL,
    });
    const p = await ctx.newPage();
    await apiSignIn(p, uniqueEmail(who));
    const view = await call<{ invite_token: string }>(page, "GET", `/houses/${apt.id}`);
    await call(p, "POST", `/join/${view.invite_token}/claim`, { member_id: apt.member[who] });
    return p;
  }
  const arjun = await roommate("arjun"); // joined (Pranav is the admin)
  const kabir = await roommate("kabir");
  // Arjun pays for groceries that include Pranav (tab).
  const { billId, itemIds } = await seedShopRite(arjun, apt, { split: "all", send: true });
  // Arjun ticks Pranav onto the yogurt (share).
  await call(arjun, "PUT", `/bills/${billId}/allocations`, {
    changes: [{ item_id: itemIds.yogurt, participant: `m:${apt.member.pranav}`, on: true }],
  });
  // The share change is in Pranav's inbox at once (drained here as production does within seconds);
  // its push and email wait out a 10-minute window so a burst of ticks makes one message (COALESCE
  // in notify.py, tested in test_notify.py), and a tab settled meanwhile supersedes it.
  await expect(async () => {
    await page.request.post(`${API_ORIGIN}/__test/drain`);
    const inbox = await call<{ items: { title: string }[] }>(page, "GET", "/me/inbox");
    expect(inbox.items.map((i) => i.title)).toContain("Your ShopRite share changed");
  }).toPass({ timeout: 20_000, intervals: [500, 1000, 2000] });
  // Arjun nudges Pranav (remind).
  await call(arjun, "POST", `/bills/${billId}/remind`, { member_id: apt.member.pranav });
  // Pranav pays, Arjun confirms (pay).
  const bill = await call<{ shares: Record<string, number> }>(page, "GET", `/bills/${billId}`);
  const share = bill.shares[`m:${apt.member.pranav}`]!;
  const payment = await call<{ id: string }>(page, "POST", `/bills/${billId}/payments`, {
    amount_cents: share,
    method: "cash",
  });
  await call(arjun, "POST", `/payments/${payment.id}/confirm`);
  // Kabir asks for Pranav's spinach (asks); Pranav asks for Arjun's bars, Arjun says yes (answers).
  const fridge = await call<{ areas: { items: { id: string; name: string }[] }[] }>(
    page,
    "GET",
    `/houses/${apt.id}/inventory?place=fridge`,
  );
  const pantry = await call<{ areas: { items: { id: string; name: string }[] }[] }>(
    page,
    "GET",
    `/houses/${apt.id}/inventory?place=pantry`,
  );
  const item = (name: string) =>
    [...fridge.areas, ...pantry.areas].flatMap((a) => a.items).find((i) => i.name === name)!;
  await call(kabir, "POST", `/inventory/${item("Baby spinach").id}/requests`, { amount: "a_bit" });
  const ask = await call<{ id: string }>(
    page,
    "POST",
    `/inventory/${item("Protein bars 12 ct").id}/requests`,
    { amount: "a_bit" },
  );
  await call(arjun, "POST", `/requests/${ask.id}/answer`, { decision: "free" });
  // Arjun stamps it settled (settled).
  await call(arjun, "POST", `/bills/${billId}/settle`, { confirm_outstanding: true });

  const expected = [
    "Arjun joined Apt 4B",
    "Arjun split a ShopRite run",
    "Quick nudge from Arjun",
    `Arjun confirmed your $${(share / 100).toFixed(2)}`,
    "Kabir asked for your baby spinach",
    /^Arjun said yes to /,
    "ShopRite is settled",
  ];
  await expect(async () => {
    await page.request.post(`${API_ORIGIN}/__test/drain`);
    const titles = (
      (await (await page.request.get(`${FAKE_PUSH}/__test/push?user=${device}`)).json()) as {
        payload: { title?: string };
      }[]
    ).map((r) => r.payload.title ?? "");
    for (const want of expected) {
      expect(
        titles.some((t) => (typeof want === "string" ? t === want : want.test(t))),
        String(want),
      ).toBe(true);
    }
  }).toPass({ timeout: 60_000, intervals: [1000, 2000, 3000] });
  await arjun.context().close();
  await kabir.context().close();
});
