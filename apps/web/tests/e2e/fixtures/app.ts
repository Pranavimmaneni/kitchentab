import { randomUUID } from "node:crypto";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { API_ORIGIN, TEST_INBOX_SECRET } from "./env";

/** The mockup ShopRite receipt (apps/api/tests/test_bills.py LINES) and its split (`shoprite_sent`). */
export const LINES = [
  { key: "milk", name: "Whole milk 1 gal", cents: 429, storage: "fridge" },
  { key: "eggs", name: "Eggs 18 ct", cents: 549, storage: "fridge" },
  { key: "rice", name: "Basmati rice 10 lb", cents: 1499, storage: "pantry" },
  { key: "yogurt", name: "Greek yogurt 32 oz", cents: 679, storage: "fridge" },
  { key: "bananas", name: "Bananas 2.1 lb", cents: 132, storage: "pantry" },
  { key: "towels", name: "Paper towels 6 pk", cents: 999, storage: "none" },
  { key: "soap", name: "Dish soap", cents: 349, storage: "none" },
  { key: "chicken", name: "Chicken thighs 3 lb", cents: 1137, storage: "fridge" },
  { key: "oatmilk", name: "Oat milk", cents: 459, storage: "fridge" },
  { key: "spinach", name: "Baby spinach", cents: 399, storage: "fridge" },
  { key: "bars", name: "Protein bars 12 ct", cents: 1799, storage: "pantry" },
  { key: "coffee", name: "Coffee beans 12 oz", cents: 999, storage: "pantry" },
] as const;

export type Roomie = "pranav" | "arjun" | "meera" | "kabir";

export const WHO: Record<(typeof LINES)[number]["key"], Roomie[]> = {
  milk: ["pranav", "arjun", "meera"],
  eggs: ["pranav", "arjun", "meera", "kabir"],
  rice: ["pranav", "arjun", "meera", "kabir"],
  yogurt: ["meera"],
  bananas: ["pranav", "kabir"],
  towels: ["pranav", "arjun", "meera", "kabir"],
  soap: ["pranav", "arjun", "meera", "kabir"],
  chicken: ["pranav", "arjun"],
  oatmilk: ["kabir"],
  spinach: ["pranav", "meera"],
  bars: ["arjun"],
  coffee: ["pranav", "kabir"],
};

/** `shoprite_sent` in packages/fixtures/money_cases.json. */
export const SHARES = { pranav: "$23.51", arjun: "$33.91", meera: "$18.87", kabir: "$18.89" } as const;

/** Unique per test (5 codes per email per hour); "pranav+…" still shows as "Pranav". */
export function uniqueEmail(name = "pranav"): string {
  return `${name}+${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}@example.com`;
}

/** Reads the newest sign-in code sent to `email` from the test inbox (ADR-0016). */
export async function readCode(request: APIRequestContext, email: string): Promise<string> {
  let code = "";
  await expect(async () => {
    const res = await request.get(`${API_ORIGIN}/api/test/inbox?to=${encodeURIComponent(email)}`, {
      headers: { "X-Test-Secret": TEST_INBOX_SECRET },
    });
    expect(res.ok()).toBe(true);
    const messages = (await res.json()) as { subject: string }[];
    const match = /(\d{6})/.exec(messages[0]?.subject ?? "");
    expect(match).not.toBeNull();
    code = match![1]!;
  }).toPass({ timeout: 15_000, intervals: [100, 250, 500] });
  return code;
}

/** Types a code into the OTP field; it auto-submits at six digits. */
export async function enterCode(page: Page, email: string): Promise<void> {
  await page.waitForURL("**/verify");
  await hydrated(page);
  const code = await readCode(page.request, email);
  await page.getByLabel("6-digit code").fill(code);
}

/** Waits until React has hydrated (typing earlier would be reset by hydration). */
export async function hydrated(page: Page): Promise<void> {
  await page.locator("html[data-hydrated]").waitFor({ state: "attached" });
}

/** A · B through the UI: email, code, then wherever B sends you. */
export async function signInWithEmail(page: Page, email: string): Promise<void> {
  await page.goto("/start");
  await hydrated(page);
  await page.getByLabel("YOUR EMAIL").fill(email);
  await page.getByRole("button", { name: "Send me a code →" }).click();
  await enterCode(page, email);
  await page.waitForURL((url) => !url.pathname.startsWith("/verify"));
}

/** Records Web Share calls instead of opening the OS sheet. */
export async function stubShare(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __shared: ShareData[] };
    w.__shared = [];
    Object.defineProperty(navigator, "share", {
      configurable: true,
      value: async (data: ShareData) => {
        w.__shared.push(data);
      },
    });
    Object.defineProperty(navigator, "canShare", { configurable: true, value: () => true });
  });
}

export async function lastShared(page: Page): Promise<ShareData | undefined> {
  return page.evaluate(() => {
    const list = (window as unknown as { __shared: ShareData[] }).__shared;
    return list[list.length - 1];
  });
}

type CallOptions = { ifMatch?: number; key?: string };

/** Calls the API as the page's signed-in user (CSRF from the cookie, keys per call). */
export async function call<T>(
  page: Page,
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
  { ifMatch, key }: CallOptions = {},
): Promise<T> {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "kt_csrf")?.value ?? "";
  const headers: Record<string, string> = {
    "X-CSRF-Token": csrf,
    "X-Client-Mutation-Id": randomUUID(),
  };
  if (method === "POST") headers["Idempotency-Key"] = key ?? randomUUID();
  if (ifMatch !== undefined) headers["If-Match"] = `"${ifMatch}"`;
  const res = await page.request.fetch(`/api/v1${path}`, { method, data: body, headers });
  if (!res.ok()) throw new Error(`${method} ${path} → ${res.status()} ${await res.text()}`);
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

type HouseView = { house: { id: string; name: string; version: number }; members: { id: string; display_name: string }[] };

/** Creates "Apt 4B" with Arjun, Meera and Kabir for the signed-in page. */
export async function createHouse(page: Page, name = "Apt 4B"): Promise<{ id: string; member: Record<Roomie, string> }> {
  const created = await call<HouseView>(page, "POST", "/houses", {
    name,
    roommates: ["Arjun", "Meera", "Kabir"],
    timezone: "America/New_York",
  });
  const byName = Object.fromEntries(created.members.map((m) => [m.display_name.toLowerCase(), m.id]));
  const me = created.members.find((m) => !["arjun", "meera", "kabir"].includes(m.display_name.toLowerCase()))!;
  return {
    id: created.house.id,
    member: { pranav: me.id, arjun: byName.arjun!, meera: byName.meera!, kabir: byName.kabir! },
  };
}

type Seeded = { billId: string; itemIds: Record<string, string>; linkToken?: string };

/**
 * The ShopRite draft through the API: header, 12 items, tax $0.89, printed total $95.18, then
 * optionally the `shoprite_sent` split (all or all but `leave` items) and send.
 */
export async function seedShopRite(
  page: Page,
  house: { id: string; member: Record<Roomie, string> },
  { split = "none", send = false, leave = [] as string[] }: { split?: "none" | "all"; send?: boolean; leave?: string[] } = {},
): Promise<Seeded> {
  const bill = await call<{ id: string; version: number }>(page, "POST", `/houses/${house.id}/bills`, {
    store_name: "ShopRite",
    purchase_date: "2026-10-02",
    receipt_total_cents: 9518,
  });
  await call(page, "PATCH", `/bills/${bill.id}`, { tax_cents: 89 }, { ifMatch: bill.version });
  const itemIds: Record<string, string> = {};
  for (const line of LINES) {
    const item = await call<{ id: string }>(page, "POST", `/bills/${bill.id}/items`, {
      name: line.name,
      line_total_cents: line.cents,
      storage: line.storage,
    });
    itemIds[line.key] = item.id;
  }
  if (split === "all") {
    const changes = LINES.filter((l) => !leave.includes(l.key)).flatMap((l) =>
      WHO[l.key].map((who) => ({ item_id: itemIds[l.key], participant: `m:${house.member[who]}`, on: true })),
    );
    await call(page, "PUT", `/bills/${bill.id}/allocations`, { changes });
  }
  let linkToken: string | undefined;
  if (send) {
    const out = await call<{ link_token: string }>(page, "POST", `/bills/${bill.id}/send`);
    linkToken = out.link_token;
  }
  return { billId: bill.id, itemIds, linkToken };
}
