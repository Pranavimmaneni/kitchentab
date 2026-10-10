import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { LINES, WHO, call, createHouse, hydrated, readCode, uniqueEmail, type Roomie } from "./app";
import { API_ORIGIN } from "./env";
import { profileFor } from "./roommates";

/**
 * Milestone 7–8 journeys: a household of real signed-in members on separate browser contexts, set
 * up through the API (screens A–D have their own journeys), plus kitchen and tab seeders.
 */

/** Signs the page's browser context in with an email code through the API (test inbox). */
export async function apiSignIn(page: Page, email: string): Promise<void> {
  const csrf = await page.request.get("/api/v1/auth/csrf");
  expect(csrf.ok()).toBe(true);
  await call(page, "POST", "/auth/otp/send", { channel: "email", destination: email });
  const code = await readCode(page.request, email);
  await call(page, "POST", "/auth/otp/verify", { channel: "email", destination: email, code });
}

export type Apt = {
  id: string;
  member: Record<Roomie, string>;
  inviteToken: string;
  pages: Partial<Record<Roomie, Page>>;
  contexts: BrowserContext[];
};

/**
 * Pranav signs in and creates Apt 4B (Arjun, Meera, Kabir as placeholders); each name in `claim`
 * gets its own phone-sized context, signs in and claims its placeholder.
 */
export async function aptWith(
  browser: Browser,
  baseURL: string | undefined,
  payer: Page,
  claim: Roomie[] = [],
): Promise<Apt> {
  await apiSignIn(payer, uniqueEmail("pranav"));
  const house = await createHouse(payer);
  const view = await call<{ invite_token: string }>(payer, "GET", `/houses/${house.id}`);
  const apt: Apt = {
    ...house,
    inviteToken: view.invite_token,
    pages: { pranav: payer },
    contexts: [],
  };
  for (const [i, who] of claim.entries()) {
    const { options } = profileFor(browser, i + 1);
    const context = await browser.newContext({ ...options, baseURL });
    apt.contexts.push(context);
    const page = await context.newPage();
    await apiSignIn(page, uniqueEmail(who));
    await call(page, "POST", `/join/${apt.inviteToken}/claim`, { member_id: house.member[who] });
    apt.pages[who] = page;
  }
  return apt;
}

export async function closeApt(apt: Apt): Promise<void> {
  await Promise.all(apt.contexts.map((c) => c.close()));
}

/** Icons and categories from tests/scenarios/apt4b.yaml so tiles look like the mockups. */
const KITCHEN_META: Record<string, { icon_key?: string; category?: string }> = {
  milk: { icon_key: "milk", category: "dairy" },
  eggs: { icon_key: "eggs", category: "eggs" },
  rice: { icon_key: "rice", category: "grains" },
  yogurt: { icon_key: "yogurt", category: "dairy" },
  bananas: { icon_key: "banana", category: "produce" },
  towels: { category: "household" },
  soap: { category: "household" },
  chicken: { icon_key: "chicken", category: "meat_fish" },
  oatmilk: { icon_key: "plant_milk", category: "drinks" },
  spinach: { icon_key: "leafy_greens", category: "produce" },
  bars: { icon_key: "bar", category: "snacks" },
  coffee: { icon_key: "coffee", category: "coffee_tea" },
};

/** Today in the house time zone (America/New_York) on the API's clock, minus `daysAgo`. */
export async function houseDate(page: Page, daysAgo = 0): Promise<string> {
  const res = await page.request.get(`${API_ORIGIN}/__test/clock`);
  const now = res.ok() ? new Date(((await res.json()) as { now: string }).now) : new Date();
  const local = now.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const [y, m, d] = local.split("-").map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d - daysAgo)).toISOString().slice(0, 10);
}

type Seeded = { billId: string; itemIds: Record<string, string> };

/**
 * The ShopRite receipt split like `shoprite_sent` (Meera can join the coffee) and sent, which
 * stocks the fridge and pantry. `daysAgo` sets the purchase date: 4 days ago puts Baby spinach
 * (5-day shelf life) at 80% (USE SOON) and Chicken thighs (2 days) past 100% (STILL THERE?).
 */
export async function seedShopRite(
  page: Page,
  apt: Pick<Apt, "id" | "member">,
  {
    daysAgo = 0,
    date,
    meeraOnCoffee = false,
  }: { daysAgo?: number; date?: string; meeraOnCoffee?: boolean } = {},
): Promise<Seeded> {
  const purchase = date ?? (await houseDate(page, daysAgo));
  const bill = await call<{ id: string; version: number }>(
    page,
    "POST",
    `/houses/${apt.id}/bills`,
    {
      store_name: "ShopRite",
      purchase_date: purchase,
      receipt_total_cents: 9518,
    },
  );
  await call(page, "PATCH", `/bills/${bill.id}`, { tax_cents: 89 }, { ifMatch: bill.version });
  const itemIds: Record<string, string> = {};
  for (const line of LINES) {
    const item = await call<{ id: string }>(page, "POST", `/bills/${bill.id}/items`, {
      name: line.name,
      line_total_cents: line.cents,
      storage: line.storage,
      ...KITCHEN_META[line.key],
    });
    itemIds[line.key] = item.id;
  }
  const who = (key: (typeof LINES)[number]["key"]): Roomie[] =>
    key === "coffee" && meeraOnCoffee ? ["pranav", "meera", "kabir"] : WHO[key];
  const changes = LINES.flatMap((l) =>
    who(l.key).map((name) => ({
      item_id: itemIds[l.key],
      participant: `m:${apt.member[name]}`,
      on: true,
    })),
  );
  await call(page, "PUT", `/bills/${bill.id}/allocations`, { changes });
  await call(page, "POST", `/bills/${bill.id}/send`);
  return { billId: bill.id, itemIds };
}

/** The H Mart tab Meera paid for (apt4b.yaml): Pranav owes her $14.10 on it. */
export async function seedHMart(
  page: Page,
  apt: Pick<Apt, "id" | "member">,
  { daysAgo = 12 }: { daysAgo?: number } = {},
): Promise<string> {
  const lines: [string, number, string, Roomie[], string?][] = [
    ["Frozen dumplings", 899, "freezer", ["arjun", "kabir"], "dumplings"],
    ["Ice cream", 649, "freezer", ["meera"], "ice_cream"],
    ["Kimchi", 799, "fridge", ["meera", "kabir"], "kimchi_jar"],
    ["Firm tofu", 249, "fridge", ["meera"], "tofu"],
    ["Shin ramyun 4 pk", 599, "pantry", ["pranav", "arjun", "meera", "kabir"], "noodles"],
    ["Korean pear box", 1499, "pantry", ["pranav", "meera"], "apple"],
    ["Rice cooker cleaner", 1020, "none", ["pranav", "arjun"]],
    ["Dish gloves", 299, "none", ["meera"]],
    ["Scrub sponges", 227, "none", ["arjun", "kabir"]],
  ];
  const bill = await call<{ id: string }>(page, "POST", `/houses/${apt.id}/bills`, {
    store_name: "H Mart",
    purchase_date: await houseDate(page, daysAgo),
    payer_member_id: apt.member.meera,
    receipt_total_cents: 6240,
  });
  const changes = [];
  for (const [name, cents, storage, who, icon] of lines) {
    const item = await call<{ id: string }>(page, "POST", `/bills/${bill.id}/items`, {
      name,
      line_total_cents: cents,
      storage,
      ...(icon ? { icon_key: icon } : {}),
    });
    changes.push(
      ...who.map((w) => ({ item_id: item.id, participant: `m:${apt.member[w]}`, on: true })),
    );
  }
  await call(page, "PUT", `/bills/${bill.id}/allocations`, { changes });
  await call(page, "POST", `/bills/${bill.id}/send`);
  return bill.id;
}

/**
 * `call`, waiting out a 429 (60 writes per minute per user, SEC-05) instead of failing: seeding
 * long lists goes over the limit on purpose.
 */
export async function callPatiently<T>(
  page: Page,
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await call<T>(page, method, path, body);
    } catch (error) {
      const match = /→ 429 .*"retry_after":(\d+)/.exec(String(error));
      if (!match || attempt >= 3) throw error;
      await page.waitForTimeout((Number(match[1]) + 1) * 1000);
    }
  }
}

/** A one-line tab the payer alone ate, sent and settled (settled history, 20 per page). */
export async function settledTab(
  page: Page,
  apt: Pick<Apt, "id">,
  payerMemberId: string,
  store: string,
  date: string,
) {
  const bill = await callPatiently<{ id: string }>(page, "POST", `/houses/${apt.id}/bills`, {
    store_name: store,
    purchase_date: date,
  });
  const item = await callPatiently<{ id: string }>(page, "POST", `/bills/${bill.id}/items`, {
    name: "Paper towels",
    line_total_cents: 500,
    storage: "none",
  });
  await callPatiently(page, "PUT", `/bills/${bill.id}/allocations`, {
    changes: [{ item_id: item.id, participant: `m:${payerMemberId}`, on: true }],
  });
  await callPatiently(page, "POST", `/bills/${bill.id}/send`);
  await callPatiently(page, "POST", `/bills/${bill.id}/settle`, { confirm_outstanding: true });
  return bill.id;
}

/** Leftovers straight through the API (FR-79). */
export async function addLeftovers(
  page: Page,
  apt: Pick<Apt, "id">,
  body: {
    name: string;
    place?: "fridge" | "freezer" | "pantry";
    icon_key?: string;
    owner_member_ids?: string[];
  },
): Promise<string> {
  const out = await callPatiently<{ id: string }>(
    page,
    "POST",
    `/houses/${apt.id}/inventory`,
    body,
  );
  return out.id;
}

/** Opens a signed-in screen and waits for hydration. */
export async function open(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await hydrated(page);
}

/** The tile for an item (its accessible name starts with the item name). */
export function tile(page: Page, name: string) {
  return page.getByRole("button", { name: new RegExp(`^${name}, owned by `) });
}
