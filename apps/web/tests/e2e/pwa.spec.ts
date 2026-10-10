import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { createHouse, hydrated, uniqueEmail } from "./fixtures/app";
import { apiSignIn } from "./fixtures/household";

/**
 * Installable app (PRD §20.1, §21.6): manifest, service worker install/precache/offline page,
 * update flow, 426 refresh prompt, install flows per platform, standalone detection and the
 * "sign in once more" screen.
 */

type Manifest = {
  id: string;
  name: string;
  short_name: string;
  start_url: string;
  scope: string;
  display: string;
  display_override: string[];
  orientation: string;
  background_color: string;
  theme_color: string;
  icons: { src: string; sizes: string; type: string; purpose: string }[];
  shortcuts: { name: string; url: string }[];
  screenshots: { src: string; sizes: string; type: string; form_factor: string; label: string }[];
};

async function signedIn(page: Page): Promise<string> {
  await apiSignIn(page, uniqueEmail("pranav"));
  return (await createHouse(page)).id;
}

/** Waits until a service worker controls the page. */
async function controlled(page: Page): Promise<string> {
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller), undefined, {
    timeout: 30_000,
  });
  return page.evaluate(() => navigator.serviceWorker.controller!.scriptURL);
}

/** Matches `(display-mode: standalone)` as an installed app launch does. */
async function asStandalone(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    const real = window.matchMedia.bind(window);
    window.matchMedia = (query: string) => {
      if (query.includes("display-mode: standalone")) {
        return {
          matches: true,
          media: query,
          onchange: null,
          addEventListener() {},
          removeEventListener() {},
          addListener() {},
          removeListener() {},
          dispatchEvent: () => false,
        } as MediaQueryList;
      }
      return real(query);
    };
  });
}

test("PWA-01 the manifest is valid: name, id, start_url, standalone, icons incl. maskable and monochrome, shortcuts, two narrow screenshots [FR-01]", async ({
  request,
}) => {
  const res = await request.get("/manifest.webmanifest");
  expect(res.ok()).toBe(true);
  expect(res.headers()["content-type"]).toContain("application/manifest+json");
  const m = (await res.json()) as Manifest;
  expect(m).toMatchObject({
    id: "/",
    name: "KitchenTab",
    short_name: "KitchenTab",
    start_url: "/?source=pwa",
    scope: "/",
    display: "standalone",
    display_override: ["standalone"],
    orientation: "portrait",
    background_color: "#ECEAE2",
    theme_color: "#121210",
  });
  const purposes = m.icons.map((i) => `${i.sizes}:${i.purpose}`);
  expect(purposes).toEqual(
    expect.arrayContaining(["192x192:any", "512x512:any", "512x512:maskable", "any:monochrome"]),
  );
  expect(m.shortcuts.map((s) => s.url)).toEqual([
    "/bills/new",
    "/kitchen/fridge",
    "/kitchen/requests",
  ]);
  expect(m.screenshots).toHaveLength(2);
  for (const s of m.screenshots) {
    expect(s).toMatchObject({ form_factor: "narrow", type: "image/png", sizes: "780x1688" });
    expect(s.label.length).toBeGreaterThan(10);
  }
  for (const url of [
    ...m.icons.map((i) => i.src),
    ...m.screenshots.map((s) => s.src),
    "/icons/apple-touch-icon.png",
    "/icons/badge-96.png",
  ]) {
    const asset = await request.get(url);
    expect(asset.ok(), url).toBe(true);
    expect(asset.headers()["content-type"], url).toMatch(/^image\/(png|svg\+xml)/);
  }
});

test("PWA-10 theme-color meta tags follow light and dark [FR-01]", async ({ page }) => {
  await page.goto("/start");
  const metas = await page
    .locator('meta[name="theme-color"]')
    .evaluateAll((els) => els.map((e) => [e.getAttribute("media"), e.getAttribute("content")]));
  expect(metas).toEqual(
    expect.arrayContaining([
      ["(prefers-color-scheme: light)", "#ECEAE2"],
      ["(prefers-color-scheme: dark)", "#0D0D0B"],
    ]),
  );
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
    "href",
    /manifest\.webmanifest/,
  );
});

test("PWA-11 the app shortcuts open the right screens [FR-01] [FR-60]", async ({ page }) => {
  await signedIn(page);
  await page.goto("/bills/new");
  await expect(page.getByRole("dialog", { name: "Snap a receipt" })).toBeVisible();
  await page.goto("/kitchen/fridge");
  await expect(page).toHaveURL(/\/kitchen\/fridge$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/fridge/i);
  await page.goto("/kitchen/requests");
  await expect(page).toHaveURL(/\/kitchen\/requests$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("AUTH-13 the first launch from the home screen without a session says why it asks again [FR-01]", async ({
  page,
  context,
}) => {
  await asStandalone(context);
  await page.goto("/?source=pwa");
  await expect(page).toHaveURL(/\/start/);
  await expect(page.getByText("Welcome back. Sign in once more on this app")).toBeVisible();
  await expect(page.getByLabel("YOUR EMAIL")).toBeVisible();
  // The same sign-in screen in a browser tab doesn't say it.
  const tab = await context.browser()!.newPage();
  await tab.goto(page.url());
  await hydrated(tab);
  await expect(tab.getByText("Welcome back. Sign in once more on this app")).toBeHidden();
  await tab.close();
});

test("PWA-07 a standalone launch is detected and pwa_installed is logged once per device [FR-01]", async ({
  page,
  context,
}) => {
  await asStandalone(context);
  await signedIn(page);
  await page.goto("/");
  await hydrated(page);
  await expect
    .poll(() =>
      page.evaluate(
        () => (window.__ktEvents ?? []).filter((e) => e.name === "pwa_installed").length,
      ),
    )
    .toBe(1);
  await page.reload();
  await hydrated(page);
  await page.waitForTimeout(500);
  expect(
    await page.evaluate(
      () => (window.__ktEvents ?? []).filter((e) => e.name === "pwa_installed").length,
    ),
  ).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem("kt.pwa.tracked.pwa_installed"))).toMatch(
    /^\d+$/,
  );
});

test("SYNC-22 PWA-09 a 426 STALE_CLIENT shows the refresh prompt, even over an open sheet, and reloads only on tap [FR-01]", async ({
  page,
}) => {
  const houseId = await signedIn(page);
  await page.goto(`/bills/new?house=${houseId}`);
  await hydrated(page);
  await expect(page.getByRole("dialog", { name: "Snap a receipt" })).toBeVisible();
  await page.evaluate(() => ((window as unknown as { __marker: number }).__marker = 1));
  await page.route("**/api/v1/houses/*/bills", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 426,
          contentType: "application/problem+json",
          body: JSON.stringify({
            title: "A new version is ready",
            status: 426,
            code: "STALE_CLIENT",
          }),
        })
      : route.continue(),
  );
  await page.getByRole("button", { name: "Enter items by hand" }).click();
  const prompt = page.getByRole("alertdialog", {
    name: "This version of KitchenTab is out of date",
  });
  await expect(prompt).toBeVisible();
  await page.waitForTimeout(1000);
  expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(1); // no reload by itself
  await page.unroute("**/api/v1/houses/*/bills");
  await prompt.getByRole("button", { name: "Refresh" }).click();
  await page.waitForFunction(
    () => (window as unknown as { __marker?: number }).__marker === undefined,
  );
  await expect(prompt).toBeHidden();
});

test.describe("service worker (Chromium)", () => {
  test.skip(
    ({ browserName }) => browserName !== "chromium",
    "service worker control and CDP-level checks: Chromium",
  );
  test.use({ serviceWorkers: "allow" });

  test("PWA-02 the worker registers, precaches the shell and serves the offline page [FR-01]", async ({
    page,
    context,
  }) => {
    await signedIn(page);
    await page.goto("/");
    await hydrated(page);
    const script = await controlled(page);
    expect(script).toMatch(/\/sw(-[A-Za-z0-9-]+)?\.js$/);
    const precached = await page.evaluate(async () => {
      const out: string[] = [];
      for (const name of await caches.keys()) {
        for (const req of await (await caches.open(name)).keys())
          out.push(`${name} ${new URL(req.url).pathname}`);
      }
      return out;
    });
    expect(precached.some((e) => /precache.* \/~offline$/.test(e))).toBe(true);
    expect(precached.some((e) => /precache.* \/_next\/static\/css\//.test(e))).toBe(true);
    expect(precached.some((e) => /precache.* \/manifest\.webmanifest$/.test(e))).toBe(true);
    expect(precached.some((e) => / \/ocr\//.test(e))).toBe(false); // OCR assets only on first use

    await context.setOffline(true);
    await page.goto("/balances").catch(() => undefined); // never visited: not saved on this device
    await expect(page.getByRole("heading", { name: "You're offline." })).toBeVisible();
    await context.setOffline(false);
  });

  test("PWA-03 API reads are network-first: after 3 s the saved copy is shown, marked stale [FR-50]", async ({
    page,
    context,
  }) => {
    await signedIn(page);
    await page.goto("/tabs");
    await hydrated(page);
    await controlled(page);
    await page.reload(); // now through the worker: reads saved
    await hydrated(page);
    await expect(page.getByRole("heading", { name: /^Hey, / })).toBeVisible();
    await context.route("**/api/v1/me", async (route) => {
      await new Promise((r) => setTimeout(r, 6000));
      await route.continue().catch(() => undefined);
    });
    const started = Date.now();
    await page.reload();
    await expect(page.getByRole("heading", { name: /^Hey, / })).toBeVisible({ timeout: 5500 });
    await expect(page.getByText("SLOW CONNECTION · SHOWING SAVED TABS")).toBeVisible({
      timeout: 5500,
    });
    expect(Date.now() - started).toBeLessThan(5900);
    await context.unroute("**/api/v1/me");
  });

  test("PWA-04 auth, writes, the event stream and receipt images never land in a cache [FR-18]", async ({
    page,
  }) => {
    const houseId = await signedIn(page);
    await page.goto("/tabs");
    await hydrated(page);
    await controlled(page);
    await page.reload();
    await hydrated(page);
    // A receipt photo stored and viewed (short-lived URL to storage) plus a write.
    const bill = await page.evaluate(async (house) => {
      const csrf = document.cookie.match(/kt_csrf=([^;]+)/)![1]!;
      const h = {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "Idempotency-Key": crypto.randomUUID(),
      };
      const res = await fetch(`/api/v1/houses/${house}/bills`, {
        method: "POST",
        headers: h,
        body: "{}",
      });
      return ((await res.json()) as { id: string }).id;
    }, houseId);
    await page.goto(`/bills/${bill}/review`);
    await hydrated(page);
    await page.waitForTimeout(1500);
    const cached = await page.evaluate(async () => {
      const out: string[] = [];
      for (const name of await caches.keys()) {
        for (const req of await (await caches.open(name)).keys())
          out.push(`${req.method} ${req.url}`);
      }
      return out;
    });
    expect(cached.some((u) => u.includes("/api/v1/me"))).toBe(true); // reads are saved
    for (const entry of cached) {
      expect(entry).not.toMatch(/\/api\/v1\/auth\//);
      expect(entry).not.toMatch(/\/events/);
      expect(entry).not.toMatch(/\/receipts/);
      expect(entry).not.toMatch(/\/api\/blob-(view|upload)(\?|$)/);
      expect(entry).not.toMatch(/\/push\//);
      expect(entry).not.toMatch(/:9000\//);
      expect(entry.startsWith("GET ")).toBe(true);
    }
  });

  test("PWA-08 a new version waits; the toast never shows over an open sheet; Refresh reloads into it [FR-01]", async ({
    page,
  }) => {
    const houseId = await signedIn(page);
    await page.goto(`/bills/new?house=${houseId}`);
    await hydrated(page);
    const script = await controlled(page);
    await expect(page.getByRole("dialog", { name: "Snap a receipt" })).toBeVisible();
    await page.evaluate(() => ((window as unknown as { __marker: number }).__marker = 1));
    // Deploy: the same scope gets a new worker script; it installs and waits.
    await page.evaluate(async (url) => {
      await navigator.serviceWorker.register(`${new URL(url).pathname}?v=2`, {
        scope: "/",
        updateViaCache: "none",
      });
    }, script);
    await page.waitForFunction(
      async () => Boolean((await navigator.serviceWorker.getRegistration())?.waiting),
      undefined,
      {
        timeout: 30_000,
      },
    );
    await page.waitForTimeout(2000);
    await expect(page.getByText("New version ready")).toBeHidden(); // a sheet is open
    expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(
      1,
    );

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Snap a receipt" })).toBeHidden();
    await expect(page.getByText("New version ready")).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(
      1,
    ); // still no reload
    await page.getByRole("button", { name: "Refresh" }).click();
    await page.waitForFunction(
      () => (window as unknown as { __marker?: number }).__marker === undefined,
      undefined,
      {
        timeout: 15_000,
      },
    );
    expect(await controlled(page)).toContain("?v=2");
  });

  test("PWA-05 Chromium: the install prompt is captured; the card shows from the 2nd visit, at most twice [FR-01]", async ({
    page,
    context,
  }) => {
    await signedIn(page);
    await context.addInitScript(() => {
      (window as unknown as { __prompted: number }).__prompted = 0;
    });
    const firePrompt = (p: Page) =>
      p.evaluate(() => {
        const event = new Event("beforeinstallprompt", { cancelable: true }) as Event & {
          prompt: () => Promise<void>;
          userChoice: Promise<{ outcome: string }>;
        };
        event.prompt = async () => {
          (window as unknown as { __prompted: number }).__prompted += 1;
        };
        event.userChoice = Promise.resolve({ outcome: "accepted" });
        window.dispatchEvent(event);
        return event.defaultPrevented;
      });
    const card = (p: Page) => p.getByRole("region", { name: "Add KitchenTab to your home screen" });

    // Visit 1: captured, nothing shown.
    await page.goto("/");
    await hydrated(page);
    expect(await firePrompt(page)).toBe(true);
    await page.waitForTimeout(800);
    await expect(card(page)).toBeHidden();

    // Visit 2 (a new tab is a new visit): the card, and Install calls prompt().
    const second = await context.newPage();
    await second.goto("/tabs");
    await hydrated(second);
    await firePrompt(second);
    await expect(card(second)).toBeVisible();
    await card(second).getByRole("button", { name: "Install" }).click();
    await expect(card(second)).toBeHidden();
    expect(
      await second.evaluate(() => (window as unknown as { __prompted: number }).__prompted),
    ).toBe(1);

    // Visit 3: the second and last time.
    const third = await context.newPage();
    await third.goto("/");
    await hydrated(third);
    await firePrompt(third);
    await expect(card(third)).toBeVisible();
    await card(third).getByRole("button", { name: "Not now" }).click();
    await expect(card(third)).toBeHidden();

    // Visit 4: never again (Settings › Notifications keeps the Install button).
    const fourth = await context.newPage();
    await fourth.goto("/");
    await hydrated(fourth);
    await firePrompt(fourth);
    await fourth.waitForTimeout(1000);
    await expect(card(fourth)).toBeHidden();
    await fourth.goto("/settings/notifications");
    await hydrated(fourth);
    await firePrompt(fourth);
    await expect(fourth.getByRole("button", { name: "Install KitchenTab" })).toBeVisible();
  });
});

test.describe("iPhone (WebKit)", () => {
  test.skip(
    ({ browserName }) => browserName !== "webkit",
    "the iPhone install flow: WebKit iPhone 14",
  );

  test("PWA-06 an iPhone browser tab gets N1 with the 3 install steps; push is not offered in the tab [FR-01]", async ({
    page,
    context,
  }) => {
    await context.addInitScript(() => {
      (window as unknown as { __asked: number }).__asked = 0;
      if ("Notification" in window) {
        Notification.requestPermission = async () => {
          (window as unknown as { __asked: number }).__asked += 1;
          return "default";
        };
      }
    });
    await signedIn(page);
    await page.goto("/");
    await hydrated(page);
    await page.waitForTimeout(800);
    await expect(page.getByRole("dialog", { name: "Don't miss an ask." })).toBeHidden(); // never on a first load

    const second = await context.newPage();
    await second.goto("/tabs");
    await hydrated(second);
    const n1 = second.getByRole("dialog", { name: "Don't miss an ask." });
    await expect(n1).toBeVisible();
    await expect(n1.getByRole("tab", { name: "ON IPHONE" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(n1.getByText("IPHONE NEEDS ONE EXTRA STEP")).toBeVisible();
    await expect(
      n1.getByRole("listitem").filter({ hasText: "Tap Share in Safari’s toolbar." }),
    ).toBeVisible();
    await expect(n1.getByText("Choose “Add to Home Screen”.")).toBeVisible();
    await expect(
      n1.getByText("Open KitchenTab from your Home Screen and turn on notifications."),
    ).toBeVisible();
    await expect(n1.getByRole("button", { name: "Turn on notifications" })).toHaveCount(0);
    await n1.getByRole("button", { name: "Show me where Share is" }).click();
    await expect(n1).toBeHidden();
    await expect(second.getByRole("button", { name: "Share is down here" })).toBeVisible();
    expect(await second.evaluate(() => (window as unknown as { __asked: number }).__asked)).toBe(0);
  });
});
