import type { APIRequestContext, Page } from "@playwright/test";
import {
  call,
  createHouse,
  hydrated,
  seedShopRite,
  signInWithEmail,
  uniqueEmail,
} from "./fixtures/app";
import { API_ORIGIN } from "./fixtures/env";
import {
  aptWith,
  closeApt,
  open,
  seedShopRite as seedOpenShopRite,
  tile,
} from "./fixtures/household";
import { expect, test } from "./fixtures/roommates";
import { box, openBill } from "./fixtures/sync";

/**
 * End-to-end journeys (TEST_PLAN §4): each one walks a whole story through the UI the way a
 * roommate would, across browsers, against the real API. Pieces of each are also covered by the
 * focused specs; these prove they hold together.
 */

const COFFEE = "Coffee beans 12 oz";

/** A US number nobody else in this run uses (fictional 555-01xx range). */
function uniquePhone(): { typed: string; e164: string } {
  const area = 200 + Math.floor(Math.random() * 700);
  const line = String(Math.floor(Math.random() * 100)).padStart(2, "0");
  return { typed: `(${area}) 555-01${line}`, e164: `+1${area}55501${line}` };
}

/** The newest fake SMS code for a number (`/__test/sms`, test builds only). */
async function readSms(request: APIRequestContext, e164: string): Promise<string> {
  let code = "";
  await expect(async () => {
    const res = await request.get(`${API_ORIGIN}/__test/sms?to=${encodeURIComponent(e164)}`);
    const sent = (await res.json()) as { code: string }[];
    expect(sent.length).toBeGreaterThan(0);
    code = sent[0]!.code;
  }).toPass({ timeout: 15_000, intervals: [100, 250, 500] });
  return code;
}

/** The amount on someone's row in WHO OWES WHAT. */
function owes(page: Page, name: string) {
  return page
    .getByRole("listitem")
    .filter({ has: page.getByText(name, { exact: true }) })
    .locator(".kt-owes-row__amount");
}

test("E2E-02 a roommate opens the bill link signed out, peeks read-only, claims her name with a phone code, ticks her own item, and the payer's screen shows her new share within 2 s [FR-05] [FR-31] [FR-32] @multi", async ({
  roommates,
}) => {
  test.setTimeout(150_000);
  const house = await roommates(2);
  const [payer, meera] = house.pages as [Page, Page];
  // Every browser in the suite shares one IP; the per-IP daily phone limit is PRD §7, not this story.
  await payer.request.delete(`${API_ORIGIN}/__test/rate-limits?bucket=otp_phone_ip`);

  await signInWithEmail(payer, uniqueEmail("pranav"));
  const apt = await createHouse(payer);
  // shoprite_sent: Meera isn't on the coffee yet (packages/fixtures/money_cases.json).
  const { billId, linkToken } = await seedShopRite(payer, apt, { split: "all", send: true });
  await openBill(payer, billId);
  await expect(owes(payer, "Meera")).toHaveText("$18.87");

  // Signed out: the link shows the bill read-only, with nothing to tick.
  await meera.goto(`/b/${linkToken}`);
  await hydrated(meera);
  await expect(meera.getByText("Peek is free. Sign in to tick your items.")).toBeVisible();
  await expect(meera.getByRole("button", { name: /uses/ })).toHaveCount(0);

  // Claim Meera with a phone code.
  await meera.getByRole("button", { name: "Meera", exact: true }).click();
  await expect(meera.getByText("Hi Meera, quick check it's you:")).toBeVisible();
  await meera.getByRole("button", { name: "Phone code" }).click();
  const phone = uniquePhone();
  await meera.getByLabel("YOUR PHONE NUMBER").fill(phone.typed);
  await meera.getByRole("button", { name: "Send me a code →" }).click();
  await expect(meera.getByRole("heading", { name: "Check your texts" })).toBeVisible();
  await meera.waitForURL("**/verify");
  await hydrated(meera);
  await meera.getByLabel("6-digit code").fill(await readSms(meera.request, phone.e164));
  await meera.waitForURL(new RegExp(`/bills/${billId}$`), { timeout: 20_000 });
  await expect(meera.getByRole("heading", { name: "WHO OWES WHAT" })).toBeVisible();

  // She ticks herself on the coffee; the payer's open bill moves within 2 s.
  await meera.getByRole("tab", { name: "Items & splits" }).click();
  await expect(meera.getByRole("grid", { name: "Who's eating what" })).toBeVisible();
  const mine = box(meera, true, "Meera", COFFEE);
  await expect(mine).toHaveAttribute("aria-pressed", "false");
  await mine.click();
  await expect(mine).toHaveAttribute("aria-pressed", "true");
  // shoprite_after_meera_joins_coffee: Meera $22.24, Pranav $21.82, Kabir $17.21.
  await expect(owes(payer, "Meera")).toHaveText("$22.24", { timeout: 2000 });
  await expect(owes(payer, "Kabir")).toHaveText("$17.21", { timeout: 2000 });
});

/** The compose fake push service (infra/docker-compose.yml): mints subscriptions, decrypts pushes. */
const FAKE_PUSH = process.env.KT_E2E_FAKE_PUSH ?? "http://localhost:4010";

type Received = { user: string; payload: { title?: string; body?: string; url?: string } };

/** Registers a real (fake-service) Web Push subscription for this signed-in device. */
async function pushDevice(page: Page, user: string): Promise<void> {
  const sub = await page.request
    .post(`${FAKE_PUSH}/subscriptions`, { data: { user } })
    .then((r) => r.json() as Promise<{ endpoint: string; keys: { p256dh: string; auth: string } }>);
  await call(page, "POST", "/push/subscriptions", { ...sub, device_label: user });
}

async function pushesFor(page: Page, user: string): Promise<Received[]> {
  return page.request
    .get(`${FAKE_PUSH}/__test/push?user=${encodeURIComponent(user)}`)
    .then((r) => r.json() as Promise<Received[]>);
}

test("E2E-04 Kabir filters MINE, asks for the locked chicken, Pranav's phone gets the push, Pranav adds him to the split, the shares move and Kabir now owns it [FR-66] [FR-71] [FR-72] [FR-73]", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(180_000);
  const apt = await aptWith(browser, baseURL, page, ["arjun", "kabir"]);
  const { kabir } = apt.pages as Required<typeof apt.pages>;
  const { billId } = await seedOpenShopRite(page, apt);
  // Pranav's phone takes pushes at any hour (quiet hours, on by default, would hold them overnight).
  const device = `pranav-phone-${Date.now().toString(36)}`;
  await pushDevice(page, device);
  await call(page, "PUT", "/me/notification-prefs", { quiet_hours: false });
  const before = await call<{ shares: Record<string, number> }>(page, "GET", `/bills/${billId}`);

  // Kabir: Fridge, filter MINE (the chicken isn't his, so it dims), then ask for it anyway.
  await open(kabir, "/kitchen/fridge");
  const mineChip = kabir.getByRole("button", { name: /^MINE \d+$/ });
  await mineChip.click();
  await expect(mineChip).toHaveAttribute("aria-pressed", "true");
  await expect(tile(kabir, "Chicken thighs 3 lb")).toHaveAccessibleName(/ask first/);
  await tile(kabir, "Chicken thighs 3 lb").click();
  const sheet = kabir.getByRole("dialog", { name: "Chicken thighs 3 lb" });
  await sheet.getByRole("radio", { name: "A BIT" }).click();
  await sheet.getByRole("button", { name: "Ask Pranav & Arjun →" }).click();
  await expect(sheet.getByText("ASKED · WAITING ON AN OWNER")).toBeVisible();

  // Pranav's phone gets the push (through the outbox, encrypted, decrypted by the fake service).
  await expect(async () => {
    await page.request.post(`${API_ORIGIN}/__test/drain`);
    const titles = (await pushesFor(page, device)).map((r) => r.payload.title);
    expect(titles).toContain("Kabir asked for your chicken thighs");
  }).toPass({ timeout: 30_000, intervals: [500, 1000, 2000] });

  // Pranav answers: Add them to the split.
  await open(page, "/kitchen/requests");
  const card = page.getByRole("article", { name: "Kabir wants a bit of your chicken thighs" });
  await card.getByRole("radio", { name: /Add them to the split/ }).click();
  await card.getByRole("button", { name: "Say yes" }).click();
  await expect(card.getByText("YES · ADDED TO SPLIT")).toBeVisible();

  // The shares moved (Kabir up, the two owners down, the total unchanged) …
  const after = await call<{ shares: Record<string, number> }>(page, "GET", `/bills/${billId}`);
  const key = (who: "pranav" | "arjun" | "kabir") => `m:${apt.member[who]}`;
  expect(after.shares[key("kabir")]!).toBeGreaterThan(before.shares[key("kabir")]!);
  expect(after.shares[key("pranav")]!).toBeLessThan(before.shares[key("pranav")]!);
  expect(after.shares[key("arjun")]!).toBeLessThan(before.shares[key("arjun")]!);
  const sum = (s: Record<string, number>) => Object.values(s).reduce((a, b) => a + b, 0);
  expect(sum(after.shares)).toBe(sum(before.shares));
  // … and Kabir is an owner now: his sheet shows the owner controls.
  await open(kabir, "/kitchen/fridge");
  await tile(kabir, "Chicken thighs 3 lb").click();
  await expect(kabir.getByRole("switch", { name: "I'm open to sharing" })).toBeVisible();
  await closeApt(apt);
});
