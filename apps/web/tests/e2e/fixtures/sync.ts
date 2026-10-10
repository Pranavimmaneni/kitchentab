import { expect, type Page, type Request } from "@playwright/test";
import { call, createHouse, hydrated, readCode, seedShopRite, uniqueEmail, type Roomie } from "./app";
import { quietPrompts } from "./quiet";
import type { House } from "./roommates";

/**
 * Multi-roommate set-up for the M6 sync tests: sign each browser context in through the API
 * (the same OTP endpoints the screens use, code from the test inbox) so nobody gets a stray
 * "Our place" house from screen C, then the payer sends the ShopRite bill and the others claim
 * Arjun, Meera and Kabir from the invite link.
 */
export async function signInViaApi(page: Page, email: string): Promise<void> {
  const csrf = await page.request.get("/api/v1/auth/csrf");
  expect(csrf.ok()).toBe(true);
  const token = (await page.context().cookies()).find((c) => c.name === "kt_csrf")?.value ?? "";
  const headers = { "X-CSRF-Token": token };
  const sent = await page.request.post("/api/v1/auth/otp/send", {
    data: { channel: "email", destination: email },
    headers,
  });
  expect(sent.ok(), await sent.text()).toBe(true);
  const code = await readCode(page.request, email);
  const verified = await page.request.post("/api/v1/auth/otp/verify", {
    data: { channel: "email", destination: email, code },
    headers,
  });
  expect(verified.ok(), await verified.text()).toBe(true);
}

export type Apartment = {
  houseId: string;
  billId: string;
  itemIds: Record<string, string>;
  member: Record<Roomie, string>;
  inviteToken: string;
  /** The page signed in as each roommate in play (the payer is always "pranav"). */
  as: Partial<Record<Roomie, Page>>;
};

/** Pranav (payer, house admin) plus the given roommates, all on the sent ShopRite bill. */
export async function apartment(house: House, roommates: Roomie[]): Promise<Apartment> {
  const [payerPage, ...others] = house.pages;
  if (!payerPage || others.length < roommates.length) throw new Error("not enough browser contexts");
  // Screen 5 races, not the install or notification sheets (those have their own specs).
  await Promise.all(house.pages.map((p) => quietPrompts(p.context())));
  await signInViaApi(payerPage, uniqueEmail("pranav"));
  const apt = await createHouse(payerPage);
  const { billId, itemIds } = await seedShopRite(payerPage, apt, { split: "all", send: true });
  const view = await call<{ invite_token: string }>(payerPage, "GET", `/houses/${apt.id}`);
  const as: Partial<Record<Roomie, Page>> = { pranav: payerPage };
  await Promise.all(
    roommates.map(async (name, i) => {
      const page = others[i]!;
      await signInViaApi(page, uniqueEmail(name));
      await call(page, "POST", `/join/${view.invite_token}/claim`, { member_id: apt.member[name] });
      as[name] = page;
    }),
  );
  return { houseId: apt.id, billId, itemIds, member: apt.member, inviteToken: view.invite_token, as };
}

/**
 * Opens screen 5 and waits until it is interactive, its stream is up and the first view was
 * recorded (`PUT /seen`, sent once the money loads), so later request counts start clean.
 */
export async function openBill(page: Page, billId: string): Promise<void> {
  const stream = page.waitForRequest((r) => isStream(r), { timeout: 30_000 });
  const seen = page.waitForResponse((r) => r.url().endsWith(`/bills/${billId}/seen`), { timeout: 30_000 });
  await page.goto(`/bills/${billId}`);
  await hydrated(page);
  await expect(page.getByRole("heading", { name: "WHO OWES WHAT" })).toBeVisible({ timeout: 30_000 });
  await Promise.all([stream, seen]);
}

/** The house SSE stream (not the `?after=` JSON catch-up). */
export function isStream(request: Request): boolean {
  return /\/api\/v1\/houses\/[^/]+\/events(\?last_event_id=\d+)?$/.test(request.url());
}

export function isCatchUp(request: Request): boolean {
  return /\/api\/v1\/houses\/[^/]+\/events\?after=\d+$/.test(request.url());
}

/** The checkbox for `member` on `item` as `viewer` sees it ("You" for their own column). */
export function box(page: Page, viewerIsMember: boolean, member: string, item: string) {
  return page.getByRole("button", { name: `${viewerIsMember ? "You" : member} uses ${item}`, exact: true });
}
