import { expect, test } from "./fixtures/roommates";
import {
  createHouse,
  enterCode,
  hydrated,
  seedShopRite,
  signInWithEmail,
  uniqueEmail,
} from "./fixtures/app";

test.skip(({ browserName }) => browserName === "firefox", "phone journeys: WebKit and Chromium");

test("a roommate opens the sent bill link signed out, peeks read-only, claims Meera with an email code and lands on the bill @multi [FR-05]", async ({
  roommates,
}) => {
  test.setTimeout(120_000);
  const house = await roommates(2);
  const [payer, meera] = house.pages as [(typeof house.pages)[0], (typeof house.pages)[0]];

  // Roommate 0 (payer) signs in, sets up Apt 4B and sends the ShopRite bill.
  await signInWithEmail(payer, uniqueEmail("pranav"));
  const apt = await createHouse(payer);
  const { billId, linkToken } = await seedShopRite(payer, apt, { split: "all", send: true });
  expect(linkToken).toBeTruthy();

  // Roommate 1 opens the link with no session: read-only peek + picker, no edit controls.
  await meera.goto(`/b/${linkToken}`);
  await hydrated(meera);
  await expect(meera.getByRole("heading", { level: 1 })).toHaveText(
    "Pranav split a ShopRite run with you.",
  );
  await expect(meera.getByText("Peek is free. Sign in to tick your items.")).toBeVisible();
  await expect(meera.getByText("10/02 · 12 ITEMS · Apt 4B")).toBeVisible();
  await expect(meera.getByRole("button", { name: /uses/ })).toHaveCount(0);
  // Writes need a session (LINK-03's 401 is asserted on the API side).
  const write = await meera.request.put(`/api/v1/bills/${billId}/allocations`, {
    data: { changes: [] },
  });
  expect(write.status()).toBeGreaterThanOrEqual(400);

  await expect(meera.getByText("WHICH ONE ARE YOU?")).toBeVisible();
  for (const name of ["Arjun", "Meera", "Kabir"]) {
    await expect(meera.getByRole("button", { name, exact: true })).toBeVisible();
  }
  await meera.getByRole("button", { name: "Meera", exact: true }).click();
  await expect(meera.getByRole("button", { name: "Meera", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(meera.getByText("Hi Meera, quick check it's you:")).toBeVisible();
  await meera.getByRole("button", { name: "Email code" }).click();
  const email = uniqueEmail("meera");
  await meera.getByLabel("YOUR EMAIL").fill(email);
  await meera.getByRole("button", { name: "Send me a code →" }).click();
  await expect(meera.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  await enterCode(meera, email);

  // B returns to the link, the claim finishes and the bill opens as Meera.
  await meera.waitForURL(new RegExp(`/bills/${billId}$`), { timeout: 20_000 });
  await expect(meera.getByRole("heading", { name: "WHO OWES WHAT" })).toBeVisible();
  const me = await meera.request.get("/api/v1/me").then((r) => r.json());
  expect(me.houses).toHaveLength(1);
  const view = await meera.request.get(`/api/v1/bills/${billId}`).then((r) => r.json());
  const claimed = view.participants.find((p: { id: string }) => p.id === view.me.member_id);
  expect(claimed.display_name).toBe("Meera");
  expect(claimed.claimed).toBe(true);
  await expect(meera.getByText("You", { exact: true })).toBeVisible();
  // Her own who-owes row carries her share (screen 5 also offers "Pay $18.87" below).
  const myRow = meera
    .getByRole("listitem")
    .filter({ has: meera.getByText("You", { exact: true }) });
  await expect(myRow.getByText("$18.87", { exact: true })).toBeVisible();

  // Meera is gone from the picker for anyone else (FR-05).
  const fresh = await payer.request.get(`/api/v1/b/${linkToken}`).then((r) => r.json());
  expect(fresh.join.placeholders.map((m: { display_name: string }) => m.display_name)).toEqual([
    "Arjun",
    "Kabir",
  ]);
});

test("HOUSE-03 a roommate claims their placeholder from the house invite link after signing in @multi", async ({
  roommates,
}) => {
  test.setTimeout(120_000);
  const house = await roommates(2);
  const [admin, arjun] = house.pages as [(typeof house.pages)[0], (typeof house.pages)[0]];
  await signInWithEmail(admin, uniqueEmail("pranav"));
  const apt = await createHouse(admin);
  const view = await admin.request.get(`/api/v1/houses/${apt.id}`).then((r) => r.json());

  await arjun.goto(`/h/${view.invite_token}`);
  await hydrated(arjun);
  await expect(arjun.getByRole("heading", { level: 1 })).toHaveText(
    "Pranav invited you to Apt 4B.",
  );
  await expect(arjun.getByText("4 MEMBERS")).toBeVisible();
  await arjun.getByRole("button", { name: "Arjun", exact: true }).click();
  await arjun.getByRole("button", { name: "Email code" }).click();
  const email = uniqueEmail("arjun");
  await arjun.getByLabel("YOUR EMAIL").fill(email);
  await arjun.getByRole("button", { name: "Send me a code →" }).click();
  await enterCode(arjun, email);
  await arjun.waitForURL((url) => url.pathname === "/", { timeout: 20_000 });

  const after = await admin.request.get(`/api/v1/houses/${apt.id}`).then((r) => r.json());
  const member = after.members.find((m: { id: string }) => m.id === apt.member.arjun);
  expect(member).toMatchObject({ display_name: "Arjun", claimed: true, status: "active" });
  await expect(arjun.getByText("KITCHENTAB ** APT 4B").first()).toBeVisible();
});
