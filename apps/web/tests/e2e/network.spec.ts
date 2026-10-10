import type { Page, Request, Route } from "@playwright/test";
import { call } from "./fixtures/app";
import { addLeftovers, open, tile } from "./fixtures/household";
import { expect, test } from "./fixtures/roommates";
import { apartment, box, isStream, openBill } from "./fixtures/sync";

/**
 * Network failure and latency on the web side (PRD §15 Network, §21.6 Retries and Time;
 * TEST_PLAN OFFLINE-03/04, CHAOS-06/08, EDGE-15-25/26) against the real API, with Playwright
 * routes standing in for the flaky network between the browser and the API.
 */

// Several browsers per test: one after another so timings measure the app, not the machine.
test.describe.configure({ mode: "default" });

const PAYMENTS = /\/api\/v1\/bills\/[^/]+\/payments$/;

async function itemsView(page: Page): Promise<void> {
  await page.getByRole("tab", { name: "Items & splits" }).click();
  await expect(page.getByRole("grid", { name: "Who's eating what" })).toBeVisible();
}

function isPost(pattern: RegExp) {
  return (r: Request) => r.method() === "POST" && pattern.test(r.url());
}

test("EDGE-15-26 a tick whose write fails rolls back with 'Couldn't save · Retry', and Retry saves it [FR-32] [FR-33]", async ({
  roommates,
}) => {
  test.setTimeout(120_000);
  const house = await roommates(2);
  const apt = await apartment(house, ["arjun"]);
  const arjun = apt.as.arjun!;
  await openBill(arjun, apt.billId);
  await itemsView(arjun);

  // The server fails every allocation write (a 503 from a proxy or a crashed instance).
  let failing = true;
  let attempts = 0;
  await arjun.route(/\/api\/v1\/bills\/[^/]+\/allocations$/, async (route: Route) => {
    if (route.request().method() !== "PUT" || !failing) return route.continue();
    attempts += 1;
    return route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: JSON.stringify({ title: "Service unavailable", status: 503, code: "UNAVAILABLE" }),
    });
  });
  const yogurt = box(arjun, true, "Arjun", "Greek yogurt 32 oz");
  await expect(yogurt).toHaveAttribute("aria-pressed", "false");
  await yogurt.click();
  // Optimistic first …
  await expect(yogurt).toHaveAttribute("aria-pressed", "true");
  // … then rolled back with the toast and its Retry.
  await expect(arjun.getByText("Couldn't save · Retry")).toBeVisible();
  await expect(yogurt).toHaveAttribute("aria-pressed", "false");
  expect(attempts).toBe(1);

  failing = false;
  await arjun.getByRole("button", { name: "Retry" }).click();
  await expect(yogurt).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(async () => {
      const bill = await call<{
        items: { name: string; allocations: { participant: string }[] }[];
      }>(arjun, "GET", `/bills/${apt.billId}`);
      return bill.items
        .find((i) => i.name === "Greek yogurt 32 oz")!
        .allocations.map((a) => a.participant);
    })
    .toContain(`m:${apt.member.arjun}`);
});

test("OFFLINE-03 SYNC-16 a payment whose request times out is retried 3× with the same Idempotency-Key, then offers Retry; one payment in the end [FR-36]", async ({
  roommates,
}) => {
  test.setTimeout(120_000);
  const house = await roommates(2);
  const apt = await apartment(house, ["kabir"]);
  const kabir = apt.as.kabir!;
  await openBill(kabir, apt.billId);

  // The first four attempts never get an answer; after that the network is back.
  const keys: string[] = [];
  let dropped = 0;
  await kabir.route(PAYMENTS, async (route: Route) => {
    if (route.request().method() !== "POST") return route.continue();
    keys.push((await route.request().headerValue("idempotency-key")) ?? "");
    if (dropped < 4) {
      dropped += 1;
      return route.abort("timedout");
    }
    return route.continue();
  });

  await kabir.getByRole("button", { name: "Pay $18.89" }).click();
  const sheet = kabir.getByRole("dialog", { name: "Pay Pranav $18.89" });
  await sheet.getByRole("button", { name: "I've paid — mark it" }).click();
  await expect(kabir.getByText("Couldn't save · Retry")).toBeVisible({ timeout: 15_000 });
  expect(keys).toHaveLength(4); // the first try and 3 retries
  expect(new Set(keys).size).toBe(1);

  await kabir.getByRole("button", { name: "Retry" }).click();
  await expect(kabir.getByText("Marked. Pranav will confirm.")).toBeVisible();
  expect(keys).toHaveLength(5);
  expect(new Set(keys).size).toBe(1);
  const money = await call<{ payments: { amount_cents: number; status: string }[] }>(
    kabir,
    "GET",
    `/bills/${apt.billId}/money`,
  );
  expect(money.payments.filter((p) => p.amount_cents === 1889)).toHaveLength(1);
});

test("OFFLINE-04 nothing is queued while offline: a kitchen write offline is refused, and nothing goes out once back online [FR-77]", async ({
  roommates,
}) => {
  test.setTimeout(120_000);
  const house = await roommates(1);
  const apt = await apartment(house, []);
  const payer = apt.as.pranav!;
  await addLeftovers(payer, { id: apt.houseId }, { name: "Dal from Sunday", place: "fridge" });
  await open(payer, "/kitchen/fridge");

  const writes: string[] = [];
  payer.on("request", (r) => {
    if (r.method() !== "GET") writes.push(`${r.method()} ${r.url()}`);
  });
  await tile(payer, "Dal from Sunday").click();
  const sheet = payer.getByRole("dialog", { name: "Dal from Sunday" });
  await expect(sheet).toBeVisible();
  await payer.context().setOffline(true);
  await expect(payer.getByText("YOU'RE OFFLINE · SHOWING SAVED TABS")).toBeVisible();
  await sheet
    .getByRole("button", { name: "Used up" })
    .click({ force: true })
    .catch(() => undefined);
  await payer.waitForTimeout(2500); // longer than every retry back-off together (1.75 s)
  expect(writes).toEqual([]);

  await payer.context().setOffline(false);
  await expect(payer.getByText("YOU'RE OFFLINE · SHOWING SAVED TABS")).toBeHidden();
  await payer.waitForTimeout(2500);
  expect(writes).toEqual([]);
  // The item is still in the fridge: the write never happened.
  const fridge = await call<{ areas: { items: { name: string }[] }[] }>(
    payer,
    "GET",
    `/houses/${apt.houseId}/inventory?place=fridge`,
  );
  expect(fridge.areas.flatMap((a) => a.items.map((i) => i.name))).toContain("Dal from Sunday");
});

test("CHAOS-06 with 2,000 ms between the browser and the API the screen says Saving…, and a double tap sends one payment [FR-32] [FR-36]", async ({
  roommates,
}) => {
  test.setTimeout(120_000);
  const house = await roommates(2);
  const apt = await apartment(house, ["meera"]);
  const meera = apt.as.meera!;
  await openBill(meera, apt.billId);
  await itemsView(meera);

  // Every API call from Meera's browser now takes 2 s longer (the stream excepted: it is open).
  await meera.route("**/api/v1/**", async (route: Route) => {
    if (isStream(route.request())) return route.continue();
    await new Promise((resolve) => setTimeout(resolve, 2000));
    await route.continue().catch(() => undefined);
  });
  const puts: string[] = [];
  meera.on("request", (r) => {
    if (r.method() === "PUT" && r.url().endsWith("/allocations")) puts.push(r.url());
  });
  const coffee = box(meera, true, "Meera", "Coffee beans 12 oz");
  await coffee.click();
  await expect(coffee).toHaveAttribute("aria-pressed", "true");
  const saving = meera.getByRole("status").filter({ hasText: "Saving…" });
  await expect(saving).toBeVisible({ timeout: 1500 });
  await expect(saving).toBeHidden({ timeout: 8000 });
  await expect(coffee).toHaveAttribute("aria-pressed", "true");
  expect(puts).toHaveLength(1);

  await meera.getByRole("tab", { name: "Who owes" }).click();
  const posts: string[] = [];
  meera.on("request", (r) => {
    if (isPost(PAYMENTS)(r)) posts.push(r.url());
  });
  await meera.getByRole("button", { name: /^Pay \$/ }).click();
  const sheet = meera.getByRole("dialog", { name: /^Pay Pranav / });
  await sheet.getByRole("button", { name: "I've paid — mark it" }).dblclick();
  await expect(saving).toBeVisible({ timeout: 1500 });
  await expect(meera.getByText("Marked. Pranav will confirm.")).toBeVisible({ timeout: 10_000 });
  expect(posts).toHaveLength(1);
  const money = await call<{ payments: unknown[] }>(meera, "GET", `/bills/${apt.billId}/money`);
  expect(money.payments).toHaveLength(1);
});

test("CHAOS-08 phones whose clocks are 10 minutes fast or slow still read an ask from just now as JUST NOW (server time from the Date header) [FR-71] [FR-75]", async ({
  roommates,
}) => {
  test.setTimeout(150_000);
  const house = await roommates(2);
  const apt = await apartment(house, ["kabir"]);
  const [payer, kabir] = [apt.as.pranav!, apt.as.kabir!];
  const dal = await addLeftovers(
    payer,
    { id: apt.houseId },
    { name: "Dal from Sunday", place: "fridge" },
  );
  await call(kabir, "POST", `/inventory/${dal}/requests`, { amount: "a_bit" });

  // Kabir's clock runs 10 minutes fast, Pranav's 10 minutes slow.
  await kabir.clock.install({ time: new Date(Date.now() + 10 * 60_000) });
  await payer.clock.install({ time: new Date(Date.now() - 10 * 60_000) });

  await open(payer, "/kitchen/requests");
  const card = payer.getByRole("article", { name: "Kabir wants a bit of your dal" });
  await expect(card.getByText("JUST NOW")).toBeVisible();

  await open(kabir, "/kitchen/requests");
  await kabir.getByRole("tab", { name: /^YOU ASKED/ }).click();
  await expect(kabir.getByText("WAITING · JUST NOW")).toBeVisible();
  await expect(kabir.getByText(/WAITING · 10 MIN/)).toHaveCount(0);
});

test("EDGE-15-25 a tab open 60+ days shows the payer a gentle banner, never to members, and nothing settles on its own [FR-39]", async ({
  roommates,
}) => {
  test.setTimeout(120_000);
  const house = await roommates(2);
  const apt = await apartment(house, ["meera"]);
  const [payer, meera] = [apt.as.pranav!, apt.as.meera!];
  // The API's 60-day rule is its own test (apps/api tests/test_open_bill.py EDGE-15-25); here the
  // flag it sets on GET /bills/{id} comes from a server whose clock is two months on.
  // 64½ days ago: day 65 of the tab, well clear of a day boundary.
  const sentAt = new Date(Date.now() - 64.5 * 86_400_000).toISOString();
  const aged = async (route: Route) => {
    if (route.request().method() !== "GET") return route.continue();
    const res = await route.fetch();
    const body = (await res.json()) as {
      long_open?: boolean;
      me: { is_payer: boolean };
      bill: { sent_at: string };
    };
    body.long_open = body.me.is_payer;
    body.bill.sent_at = sentAt;
    return route.fulfill({ response: res, json: body });
  };
  for (const page of [payer, meera])
    await page.route(new RegExp(`/api/v1/bills/${apt.billId}$`), aged);
  await Promise.all([openBill(payer, apt.billId), openBill(meera, apt.billId)]);

  const banner = payer.getByRole("status").filter({ hasText: "This tab has been open 65 days." });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(
    "Stamp it settled once everyone's square. It never settles on its own.",
  );
  await expect(payer.getByText("STILL OPEN · DAY 65")).toBeVisible();
  await expect(meera.getByText(/This tab has been open/)).toHaveCount(0);
  await banner.getByRole("button", { name: "Dismiss" }).click();
  await expect(payer.getByText(/This tab has been open/)).toHaveCount(0);
  // Still open: the banner is a nudge, not an action.
  const bill = await call<{ bill: { state: string } }>(payer, "GET", `/bills/${apt.billId}`);
  expect(bill.bill.state).toBe("open");
});
