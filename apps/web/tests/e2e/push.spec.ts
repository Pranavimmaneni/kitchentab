import { createHash } from "node:crypto";
import { expect, test, type BrowserContext, type Page, type Worker } from "@playwright/test";
import { call, createHouse, hydrated, seedShopRite, uniqueEmail } from "./fixtures/app";
import { apiSignIn } from "./fixtures/household";

/**
 * Push on the web side (PRD §20.2): the N1 pre-prompt and its moments, permission only from a tap,
 * subscription kept current (start + pushsubscriptionchange), the worker's push and click
 * handlers, the app badge, and N2 · Settings › Notifications. Delivery through a real push
 * service can't reach a headless browser, so the worker's handlers are driven with real
 * PushEvent / NotificationEvent objects dispatched inside the worker (`make test-push` runs these
 * with --grep "PUSH-"; the API side runs against the fake push service).
 */

test.skip(
  ({ browserName }) => browserName !== "chromium",
  "push and service-worker events: Chromium",
);
// The full Chromium build (new headless): the headless shell reports Notification.permission as
// "denied" whatever is granted, so notification permission can't be exercised there.
test.use({ channel: "chromium" });

const hash16 = (s: string) => createHash("sha256").update(s).digest("hex").slice(0, 16);

type Device = { id: string; endpoint_hash: string; device_label: string };

async function signedIn(page: Page): Promise<Awaited<ReturnType<typeof createHouse>>> {
  await apiSignIn(page, uniqueEmail("pranav"));
  return createHouse(page);
}

/** A push subscription the browser "has" (headless Chromium can't reach a real push service). */
async function fakePushManager(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    const make = (endpoint: string) => ({
      endpoint,
      expirationTime: null,
      options: { applicationServerKey: null, userVisibleOnly: true },
      toJSON: () => ({ endpoint, keys: { p256dh: "B".repeat(87), auth: "A".repeat(22) } }),
      unsubscribe: async () => {
        localStorage.removeItem("stub.endpoint");
        return true;
      },
      getKey: () => null,
    });
    const current = () => localStorage.getItem("stub.endpoint");
    PushManager.prototype.getSubscription = async function () {
      const e = current();
      return (e ? make(e) : null) as unknown as PushSubscription;
    };
    PushManager.prototype.subscribe = async function () {
      const e =
        current() ??
        `https://fcm.googleapis.com/fcm/send/stub-${Math.random().toString(36).slice(2)}`;
      localStorage.setItem("stub.endpoint", e);
      return make(e) as unknown as PushSubscription;
    };
  });
}

async function worker(page: Page): Promise<Worker> {
  // Installing precaches the whole shell; on a busy machine that can take a while.
  await page.waitForFunction(async () => Boolean(await navigator.serviceWorker.ready), undefined, {
    timeout: 90_000,
  });
  if (!(await page.evaluate(() => Boolean(navigator.serviceWorker.controller)))) {
    await page.reload();
    await hydrated(page);
  }
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller), undefined, {
    timeout: 30_000,
  });
  const existing = page.context().serviceWorkers();
  return existing[0] ?? (await page.context().waitForEvent("serviceworker"));
}

test.describe("N1 and permission", () => {
  test("PUSH-01 the pre-prompt never shows on first load; it comes after the first bill is sent, and from Settings [FR-42]", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const house = await signedIn(page);
    await page.goto("/");
    await hydrated(page);
    await page.waitForTimeout(1500);
    const n1 = page.getByRole("dialog", { name: "Don't miss an ask." });
    await expect(n1).toBeHidden();

    const { billId } = await seedShopRite(page, house, { split: "all" });
    await page.goto(`/bills/${billId}/send`);
    await hydrated(page);
    await page.getByRole("button", { name: "Send it to the house ↗" }).click();
    await expect(n1).toBeVisible({ timeout: 20_000 });
    await expect(n1.getByRole("tab", { name: "ANDROID · DESKTOP" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(n1.getByText("Kabir asked for your chicken thighs")).toBeVisible();
    await n1.getByRole("button", { name: "Not now" }).click();
    await expect(n1).toBeHidden();

    await page.goto("/settings/notifications");
    await hydrated(page);
    await page.getByRole("button", { name: "Turn on", exact: true }).click();
    await expect(n1).toBeVisible();
    await expect(n1.getByRole("button", { name: "Turn on notifications" })).toBeVisible();
  });

  test("PUSH-02 permission is asked only from a tap; denied is never asked again on that device [FR-42]", async ({
    page,
    context,
  }) => {
    await context.addInitScript(() => {
      const w = window as unknown as { __asked: number };
      w.__asked = Number(sessionStorage.getItem("asked") ?? 0);
      let permission: NotificationPermission =
        (localStorage.getItem("stub.permission") as NotificationPermission) ?? "default";
      Object.defineProperty(Notification, "permission", {
        configurable: true,
        get: () => permission,
      });
      Notification.requestPermission = async () => {
        w.__asked += 1;
        permission = "denied";
        localStorage.setItem("stub.permission", "denied");
        return "denied";
      };
    });
    const house = await signedIn(page);
    await page.goto("/settings/notifications");
    await hydrated(page);
    await page.waitForTimeout(1000);
    expect(await page.evaluate(() => (window as unknown as { __asked: number }).__asked)).toBe(0);

    await page.getByRole("button", { name: "Turn on", exact: true }).click();
    const n1 = page.getByRole("dialog", { name: "Don't miss an ask." });
    await n1.getByRole("button", { name: "Turn on notifications" }).click();
    await expect(n1).toBeHidden();
    expect(await page.evaluate(() => (window as unknown as { __asked: number }).__asked)).toBe(1);
    await expect(
      page.getByText("Blocked in your browser. Email takes over for what you turned on."),
    ).toBeVisible();
    await expect(page.getByText("BLOCKED", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("kt.pwa.pushDenied"))).toBe("1");

    // The next moment (a bill sent) doesn't bring N1 back on this device.
    const { billId } = await seedShopRite(page, house, { split: "all" });
    await page.goto(`/bills/${billId}/send`);
    await hydrated(page);
    await page.getByRole("button", { name: "Send it to the house ↗" }).click();
    await page.waitForURL(/\/share$/);
    await page.waitForTimeout(2000);
    await expect(n1).toBeHidden();
  });

  test("PUSH-16 the app badge equals the inbox badge (pending asks + unseen changes) and clears [FR-42]", async ({
    page,
    context,
  }) => {
    await context.addInitScript(() => {
      const calls: (number | "clear")[] = [];
      (window as unknown as { __badge: typeof calls }).__badge = calls;
      Object.defineProperty(navigator, "setAppBadge", {
        configurable: true,
        value: async (n: number) => void calls.push(n),
      });
      Object.defineProperty(navigator, "clearAppBadge", {
        configurable: true,
        value: async () => void calls.push("clear"),
      });
    });
    await signedIn(page);
    let badge = 3;
    await context.route("**/api/v1/me/inbox*", (route) =>
      route.fulfill({ json: { items: [], unread: badge, badge, next_cursor: null } }),
    );
    await page.goto("/");
    await hydrated(page);
    const calls = () =>
      page.evaluate(() => (window as unknown as { __badge: (number | "clear")[] }).__badge);
    await expect.poll(calls).toContain(3);
    badge = 0; // seen: the next look clears it
    await page.getByRole("link", { name: "TABS" }).click();
    await expect.poll(async () => (await calls()).at(-1)).toBe("clear");
    await context.unroute("**/api/v1/me/inbox*");
  });
});

test.describe("service worker", () => {
  test.use({ serviceWorkers: "allow" });

  test("PUSH-04 PUSH-03 the subscription is registered on start, re-registered when the endpoint changed, and on pushsubscriptionchange [FR-42]", async ({
    page,
    context,
  }) => {
    test.setTimeout(120_000);
    await context.grantPermissions(["notifications"]);
    await fakePushManager(context);
    await signedIn(page);
    await page.goto("/");
    await hydrated(page);
    await worker(page);
    await page.reload(); // controlled now: the start-up sync can reach the worker's PushManager
    await hydrated(page);
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem("stub.endpoint")), { timeout: 20_000 })
      .toBeTruthy();
    const first = await page.evaluate(() => localStorage.getItem("stub.endpoint"));
    await expect
      .poll(async () =>
        (await call<Device[]>(page, "GET", "/push/subscriptions")).map((d) => d.endpoint_hash),
      )
      .toEqual([hash16(first!)]);
    const label = (await call<Device[]>(page, "GET", "/push/subscriptions"))[0]!.device_label;
    expect(label).toBe("Chrome on Android");

    // The browser's endpoint changed between launches: replaced, not added.
    const second = "https://fcm.googleapis.com/fcm/send/rotated-on-start";
    await page.evaluate((e) => localStorage.setItem("stub.endpoint", e), second);
    await page.reload();
    await hydrated(page);
    await expect
      .poll(async () =>
        (await call<Device[]>(page, "GET", "/push/subscriptions")).map((d) => d.endpoint_hash),
      )
      .toEqual([hash16(second)]);

    // The push service rotates it while the app is closed: the worker handles pushsubscriptionchange.
    const sw = await worker(page);
    const third = "https://fcm.googleapis.com/fcm/send/rotated-in-worker";
    await sw.evaluate(
      ([oldEndpoint, newEndpoint]) => {
        const sub = (endpoint: string) => ({
          endpoint,
          options: { applicationServerKey: null },
          toJSON: () => ({ endpoint, keys: { p256dh: "C".repeat(87), auth: "D".repeat(22) } }),
        });
        const event = new ExtendableEvent("pushsubscriptionchange");
        Object.defineProperty(event, "oldSubscription", { value: sub(oldEndpoint!) });
        Object.defineProperty(event, "newSubscription", { value: sub(newEndpoint!) });
        self.dispatchEvent(event);
      },
      [second, third],
    );
    await expect
      .poll(async () =>
        (await call<Device[]>(page, "GET", "/push/subscriptions")).map((d) => d.endpoint_hash),
      )
      .toEqual([hash16(third)]);
  });

  test("PUSH-15 the worker shows a push and a click focuses the open window and navigates it, else opens one [FR-42]", async ({
    page,
    context,
  }) => {
    test.setTimeout(120_000);
    await context.grantPermissions(["notifications"]);
    await signedIn(page);
    await page.goto("/");
    await hydrated(page);
    const sw = await worker(page);
    const messages: string[] = [];
    await page.exposeFunction("__swMessage", (t: string) => messages.push(t));
    await page.evaluate(() =>
      navigator.serviceWorker.addEventListener("message", (e) =>
        (window as unknown as { __swMessage: (t: string) => void }).__swMessage(
          String((e.data as { type?: string })?.type),
        ),
      ),
    );

    const shown = await sw.evaluate(async () => {
      const payload = {
        title: "Kabir paid $18.89",
        body: "Tap to confirm.",
        url: "/settings",
        tag: "pay-1",
        badge: 2,
        event_id: "e1",
      };
      self.dispatchEvent(new PushEvent("push", { data: JSON.stringify(payload) }));
      for (let i = 0; i < 50; i++) {
        const list = await (
          self as unknown as ServiceWorkerGlobalScope
        ).registration.getNotifications({ tag: "pay-1" });
        if (list.length)
          return list.map((n) => ({
            title: n.title,
            body: n.body,
            tag: n.tag,
            data: n.data as unknown,
          }));
        await new Promise((r) => setTimeout(r, 100));
      }
      return [];
    });
    expect(shown).toEqual([
      {
        title: "Kabir paid $18.89",
        body: "Tap to confirm.",
        tag: "pay-1",
        data: { url: "/settings", event_id: "e1" },
      },
    ]);
    await expect.poll(() => messages).toContain("kt:push"); // the open app catches up

    // A newer notification with the same tag replaces the older one.
    const count = await sw.evaluate(async () => {
      self.dispatchEvent(
        new PushEvent("push", {
          data: JSON.stringify({ title: "Kabir paid $20.00", url: "/settings", tag: "pay-1" }),
        }),
      );
      await new Promise((r) => setTimeout(r, 500));
      return (
        await (self as unknown as ServiceWorkerGlobalScope).registration.getNotifications({
          tag: "pay-1",
        })
      ).length;
    });
    expect(count).toBe(1);

    // Click: the open window is focused and taken to the notification's screen.
    await sw.evaluate(async () => {
      const [n] = await (self as unknown as ServiceWorkerGlobalScope).registration.getNotifications(
        { tag: "pay-1" },
      );
      self.dispatchEvent(new NotificationEvent("notificationclick", { notification: n! }));
    });
    await page.waitForURL(/\/settings$/);

    // With no window open, a click opens one at the URL. A synthetic click carries no user
    // activation, so Chromium would refuse a real openWindow(); the worker's call is recorded.
    await page.close();
    const opened = await sw.evaluate(async () => {
      const scope = self as unknown as ServiceWorkerGlobalScope & { __opened?: string };
      Object.defineProperty(scope.clients, "openWindow", {
        configurable: true,
        value: async (url: string) => {
          scope.__opened = url;
          return null;
        },
      });
      await scope.registration.showNotification("Pranav split a ShopRite run", {
        tag: "bill-1",
        data: { url: "/tabs" },
      });
      const [n] = await scope.registration.getNotifications({ tag: "bill-1" });
      scope.dispatchEvent(new NotificationEvent("notificationclick", { notification: n! }));
      for (let i = 0; i < 50 && !scope.__opened; i++) await new Promise((r) => setTimeout(r, 100));
      return {
        opened: scope.__opened,
        open: (await scope.registration.getNotifications({ tag: "bill-1" })).length,
      };
    });
    expect(opened).toEqual({ opened: `${new URL(page.url()).origin}/tabs`, open: 0 });
  });
});

test.describe("N2 · Settings › Notifications", () => {
  test.use({ serviceWorkers: "allow" });

  test("PUSH-09 PUSH-03 the prefs matrix, quiet hours, private lock screen, device list and Send a test [FR-42] [FR-43]", async ({
    page,
  }) => {
    await signedIn(page);
    // A second device of this user, registered from elsewhere.
    await call(page, "POST", "/push/subscriptions", {
      endpoint: "https://updates.push.services.mozilla.com/wpush/v2/other-laptop",
      keys: { p256dh: "E".repeat(87), auth: "F".repeat(22) },
      device_label: "Firefox on Mac",
    });
    await page.goto("/settings");
    await hydrated(page);
    await page.getByRole("link", { name: /Notifications/ }).click();
    await expect(page).toHaveURL(/\/settings\/notifications$/);
    await expect(page.getByRole("heading", { level: 1, name: "Notifications" })).toBeVisible();

    // N2 defaults.
    const push = (label: string) => page.getByRole("button", { name: `Push for ${label}` });
    const email = (label: string) => page.getByRole("button", { name: `Email for ${label}` });
    await expect(push("someone asks for your stuff")).toHaveAttribute("aria-pressed", "true");
    await expect(email("someone asks for your stuff")).toHaveAttribute("aria-pressed", "false");
    await expect(push("tabs settled or reopened")).toHaveAttribute("aria-pressed", "false");
    await expect(email("tabs settled or reopened")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Quiet hours · 10 PM – 8 AM")).toBeVisible();
    await expect(page.getByRole("switch", { name: "Quiet hours" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    // Set-state writes, saved on the server.
    await email("someone asks for your stuff").click();
    await expect(email("someone asks for your stuff")).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("switch", { name: "Private on the lock screen" }).click();
    await page.getByRole("switch", { name: "Quiet hours" }).click();
    await expect
      .poll(async () =>
        call<{ email: { asks: boolean }; private_lock_screen: boolean; quiet_hours: boolean }>(
          page,
          "GET",
          "/me/notification-prefs",
        ),
      )
      .toMatchObject({ email: { asks: true }, private_lock_screen: true, quiet_hours: false });
    await page.reload();
    await expect(email("someone asks for your stuff")).toHaveAttribute("aria-pressed", "true");

    // Devices: remove the other one.
    await expect(page.getByText("Firefox on Mac")).toBeVisible();
    await page.getByRole("button", { name: "Remove Firefox on Mac" }).click();
    await expect(page.getByText("Firefox on Mac")).toBeHidden();
    await expect(page.getByText("No devices get push yet.")).toBeVisible();
    expect(await call<Device[]>(page, "GET", "/push/subscriptions")).toEqual([]);
  });

  test("PUSH-17 Send a test from N2 queues 'It works!' for this device [FR-42]", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["notifications"]);
    await fakePushManager(context);
    await signedIn(page);
    await page.goto("/settings/notifications");
    await hydrated(page);
    // Allowed, but no subscription here yet: N1 turns it on.
    await page.getByRole("button", { name: "Turn on", exact: true }).click();
    await page
      .getByRole("dialog", { name: "Don't miss an ask." })
      .getByRole("button", { name: "Turn on notifications" })
      .click();
    await expect(page.getByText("Notifications are on.")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("PUSH IS ON")).toBeVisible();
    await expect(page.getByText("YOU'RE HERE")).toBeVisible();
    await page.getByRole("button", { name: "Send a test" }).click();
    await expect(page.getByText("Sent. It should pop up in a few seconds.")).toBeVisible();
  });
});
