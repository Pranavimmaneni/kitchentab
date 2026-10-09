import { expect, test } from "@playwright/test";
import {
  createHouse,
  hydrated,
  readCode,
  seedShopRite,
  signInWithEmail,
  uniqueEmail,
} from "./fixtures/app";

test.skip(({ browserName }) => browserName === "firefox", "phone journeys: WebKit and Chromium");

test("sign-in screens follow /auth/methods, validate inline, mask the address and handle a wrong code", async ({
  page,
  context,
}) => {
  const methods = await page.request.get("/api/v1/auth/methods").then((r) => r.json());
  await page.goto("/start");
  await hydrated(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Split the groceries.Keep the roommates.");
  await expect(page.getByText("*** RECEIPT NO. 0001 ***")).toBeVisible();
  // Google only with keys; the phone tab only when SMS is configured (BUILD_ALL C3).
  await expect(page.getByRole("link", { name: "Continue with Google" })).toHaveCount(methods.google ? 1 : 0);
  await expect(page.getByRole("tab", { name: "PHONE" })).toHaveCount(methods.phone ? 1 : 0);

  await page.getByLabel("YOUR EMAIL").fill("not-an-email");
  await page.getByRole("button", { name: "Send me a code →" }).click();
  await expect(page.getByText("That doesn't look like a valid email.")).toBeVisible();
  await expect(page.getByLabel("YOUR EMAIL")).toHaveAttribute("aria-invalid", "true");

  const email = uniqueEmail("pranav");
  await page.getByLabel("YOUR EMAIL").fill(email);
  await page.getByRole("button", { name: "Send me a code →" }).click();
  await page.waitForURL("**/verify");
  await hydrated(page);
  await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  await expect(page.getByText("p***@example.com")).toBeVisible();
  await expect(page.getByText(/RESEND IN 0:\d\d/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Use a different email" })).toBeVisible();

  const code = await readCode(page.request, email);
  const verified = page.waitForResponse((r) => r.url().endsWith("/auth/otp/verify") && r.ok());
  const wrong = code === "000000" ? "111111" : "000000";
  await page.getByLabel("6-digit code").fill(wrong);
  await expect(page.getByText("That code didn't match. 4 tries left.")).toBeVisible();
  await page.getByLabel("6-digit code").fill(code);
  await page.waitForURL("**/houses/new");

  const setCookie = ((await (await verified).headersArray()).filter((h) => h.name.toLowerCase() === "set-cookie"))
    .map((h) => h.value)
    .find((v) => v.startsWith("kt_session="));
  expect(setCookie).toMatch(/HttpOnly/i);
  expect(setCookie).toMatch(/SameSite=lax/i);
  expect((await context.cookies()).some((c) => c.name === "kt_session" && c.httpOnly)).toBe(true);

  // Already signed in: /start goes home.
  await page.goto("/start");
  await expect(page).toHaveURL(/\/(houses\/new)?$/);
});

test("signed-in screens send people with no session to /start", async ({ page }) => {
  await page.goto("/bills/00000000-0000-0000-0000-000000000000/review");
  await expect(page).toHaveURL(/\/start$/);
  await page.goto("/verify");
  await expect(page).toHaveURL(/\/start$/);
});

test("LINK-04 bill and invite link pages send noindex and no-referrer", async ({ page, request, browserName }) => {
  await signInWithEmail(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { linkToken } = await seedShopRite(page, house, { split: "all", send: true });
  const invite = await page.request.get(`/api/v1/houses/${house.id}`).then((r) => r.json());

  for (const path of [`/b/${linkToken}`, `/h/${invite.invite_token}`]) {
    const res = await request.get(path, { maxRedirects: 0 });
    expect(res.status(), path).toBe(200);
    expect(res.headers()["referrer-policy"]).toBe("no-referrer");
    expect(res.headers()["x-robots-tag"]).toBe("noindex");
    const html = await res.text();
    expect(html).toMatch(/<meta name="robots" content="noindex, nofollow/);
    expect(html).toContain('<meta name="referrer" content="no-referrer"');
  }

  // One unknown token per run (20 bad links per IP per hour lock the IP out, LINK-06).
  if (browserName === "chromium") {
    await page.context().clearCookies();
    await page.goto("/h/thisLinkWasRotatedAway00");
    await expect(page.getByRole("heading", { name: "*** NO SUCH TAB ***" })).toBeAttached();
    await expect(page.locator(".kt-not-found__title")).toHaveText("*** NO SUCH TAB ***");
    await expect(page.getByText("This link has expired. Ask your house for a new one.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Go home" })).toBeVisible();
  }
});

test("LINK-05 the bill link preview is a 1200×630 PNG with store, date, total and split ways, never names or amounts per person", async ({
  page,
  request,
}) => {
  await signInWithEmail(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  const { linkToken } = await seedShopRite(page, house, { split: "all", send: true });
  const html = await request.get(`/b/${linkToken}`).then((r) => r.text());
  const og = (prop: string) =>
    new RegExp(`<meta property="og:${prop}" content="([^"]+)"`).exec(html)?.[1]?.replace(/&amp;/g, "&");
  expect(og("title")).toBe("ShopRite · $95.18");
  expect(og("description")).toBe("10/02 · SPLIT 4 WAYS · APT 4B");
  for (const leak of ["Arjun", "Meera", "Kabir", "$23.51", "$33.91", "$18.87", "$18.89"]) {
    expect(html.match(/<head>[\s\S]*<\/head>/)?.[0] ?? "").not.toContain(leak);
  }
  const imageUrl = og("image");
  expect(imageUrl).toBeTruthy();
  const image = await request.get(new URL(imageUrl!).pathname + new URL(imageUrl!).search);
  expect(image.status()).toBe(200);
  expect(image.headers()["content-type"]).toBe("image/png");
  const png = await image.body();
  expect(png.subarray(1, 4).toString("ascii")).toBe("PNG");
  expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
});
