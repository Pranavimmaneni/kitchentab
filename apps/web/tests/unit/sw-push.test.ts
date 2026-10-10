import { describe, expect, it, vi } from "vitest";
import {
  base64UrlToBytes,
  handleNotificationClick,
  handlePush,
  handleSubscriptionChange,
  notificationOptions,
  parsePush,
  safePath,
  type WindowClientLike,
} from "@/lib/pwa/sw-handlers";
import { decide, momentForWrite, pushOfferable, type DeviceState } from "@/lib/pwa/moments";
import type { PromptMemory } from "@/lib/pwa/local";

const data = (v: unknown) => ({ json: () => v, text: () => JSON.stringify(v) });

describe("push in the service worker (PRD §20.2)", () => {
  it("PUSH-15 a payload becomes a notification collapsed by tag, opening a path on this origin [FR-42]", () => {
    const p = parsePush(
      data({
        title: "Kabir paid $18.89",
        body: "Tap to confirm.",
        url: "/bills/b1",
        tag: "pay-1",
        badge: 3,
        event_id: "e1",
      }),
    );
    expect(p).toEqual({
      title: "Kabir paid $18.89",
      body: "Tap to confirm.",
      url: "/bills/b1",
      tag: "pay-1",
      badge: 3,
      event_id: "e1",
    });
    expect(notificationOptions(p)).toMatchObject({
      body: "Tap to confirm.",
      tag: "pay-1",
      renotify: true,
      data: { url: "/bills/b1" },
    });
    expect(parsePush(data({ url: "https://evil.example/x" })).url).toBe("/");
    expect(parsePush(data({ url: "//evil.example" })).url).toBe("/");
    expect(
      parsePush({
        json: () => {
          throw new Error("not json");
        },
        text: () => "hello",
      }),
    ).toMatchObject({ title: "KitchenTab", body: "hello" });
    expect(safePath("/\\evil")).toBe("/");
  });

  it("PUSH-15 PUSH-16 push shows the notification, sets the badge and tells open windows to catch up [FR-42]", async () => {
    const shown: unknown[] = [];
    const badges: number[] = [];
    const posted: unknown[] = [];
    const win = {
      url: "https://kt.test/",
      focus: vi.fn(),
      postMessage: (m: unknown) => posted.push(m),
    } as unknown as WindowClientLike;
    await handlePush(parsePush(data({ title: "T", body: "B", url: "/", badge: 2 })), {
      showNotification: async (title, o) => void shown.push([title, o.tag ?? null]),
      setAppBadge: async (n) => void badges.push(n),
      clearAppBadge: async () => void badges.push(0),
      windows: async () => [win],
    });
    expect(shown).toEqual([["T", null]]);
    expect(badges).toEqual([2]);
    expect(posted).toEqual([{ type: "kt:push", url: "/" }]);
    await handlePush(parsePush(data({ title: "T", badge: 0 })), {
      showNotification: async () => undefined,
      clearAppBadge: async () => void badges.push(-1),
      windows: async () => [],
    });
    expect(badges).toEqual([2, -1]);
  });

  it("PUSH-15 a click focuses an open KitchenTab window and navigates it, else opens one [FR-42]", async () => {
    const make = (url: string, extra: Partial<WindowClientLike> = {}) => {
      const c = {
        url,
        focus: vi.fn(async () => true),
        navigate: vi.fn(async (_url: string): Promise<unknown> => ({ url: _url })),
        postMessage: vi.fn(),
        ...extra,
      };
      return c;
    };
    const other = make("https://elsewhere.test/");
    const mine = make("https://kt.test/tabs", { visibilityState: "visible" });
    const openWindow = vi.fn(async () => null);
    expect(
      await handleNotificationClick("/bills/b1", {
        origin: "https://kt.test",
        windows: async () => [other, mine],
        openWindow,
      }),
    ).toBe("focused");
    expect(mine.focus).toHaveBeenCalled();
    expect(mine.navigate).toHaveBeenCalledWith("https://kt.test/bills/b1");
    expect(other.focus).not.toHaveBeenCalled();
    expect(openWindow).not.toHaveBeenCalled();

    const uncontrolled = make("https://kt.test/", {
      navigate: vi.fn(async () => {
        throw new TypeError("not controlled");
      }),
    });
    await handleNotificationClick("/kitchen/requests?id=a1", {
      origin: "https://kt.test",
      windows: async () => [uncontrolled],
      openWindow,
    });
    expect(uncontrolled.postMessage).toHaveBeenCalledWith({
      type: "kt:navigate",
      url: "/kitchen/requests?id=a1",
    });

    expect(
      await handleNotificationClick("/bills/b2", {
        origin: "https://kt.test",
        windows: async () => [other],
        openWindow,
      }),
    ).toBe("opened");
    expect(openWindow).toHaveBeenCalledWith("https://kt.test/bills/b2");
  });

  it("PUSH-04 pushsubscriptionchange registers the new endpoint in place of the old one [FR-42]", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const fetchMock = async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      if (url.endsWith("/auth/csrf")) return Response.json({ token: "csrf-1" });
      if (url.endsWith("/push/config")) return Response.json({ vapid_public_key: "BAAB" });
      return Response.json({ id: "s1" }, { status: 201 });
    };
    const sub = (endpoint: string) => ({
      endpoint,
      toJSON: () => ({ endpoint, keys: { p256dh: "p".repeat(40), auth: "a".repeat(16) } }),
    });
    const subscribe = vi.fn(async () => sub("https://push.test/new"));
    const ok = await handleSubscriptionChange(
      { oldSubscription: sub("https://push.test/old"), newSubscription: null },
      { origin: "https://kt.test", subscribe, fetch: fetchMock, newId: () => "id" },
    );
    expect(ok).toBe(true);
    expect(subscribe).toHaveBeenCalledWith(base64UrlToBytes("BAAB"));
    const post = calls.find(([u]) => u.endsWith("/push/subscriptions"))!;
    expect((post[1]!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(String(post[1]!.body))).toEqual({
      endpoint: "https://push.test/new",
      keys: { p256dh: "p".repeat(40), auth: "a".repeat(16) },
      replaces_endpoint: "https://push.test/old",
    });
  });
});

const device = (over: Partial<DeviceState> = {}): DeviceState => ({
  ios: false,
  standalone: false,
  pushSupported: true,
  permission: "default",
  subscribed: false,
  canPrompt: false,
  ...over,
});
const memory = (over: Partial<PromptMemory> = {}): PromptMemory => ({
  installShown: 0,
  n1NotNow: 0,
  n1SnoozedUntil: 0,
  pushDenied: false,
  ...over,
});
const NOW = 1_800_000_000_000;

describe("when install and push are offered", () => {
  it("PUSH-01 the pre-prompt never shows on a visit; after the first bill, an ask, or from Settings [FR-42]", () => {
    expect(decide("visit", device(), memory(), NOW)).toBeNull();
    expect(decide("bill", device(), memory(), NOW)).toMatchObject({
      kind: "n1",
      platform: "android",
    });
    expect(decide("ask", device(), memory(), NOW)).toMatchObject({ kind: "n1" });
    expect(
      decide(
        "settings",
        device({ permission: "granted", subscribed: true }),
        memory({ n1NotNow: 2 }),
        NOW,
      ),
    ).toMatchObject({ kind: "n1" });
    expect(momentForWrite("POST", "/bills/b1/send")).toBe("bill");
    expect(momentForWrite("POST", "/inventory/i1/requests")).toBe("ask");
    expect(momentForWrite("PUT", "/bills/b1/send")).toBeNull();
    expect(momentForWrite("POST", "/bills/b1/items")).toBeNull();
  });

  it("PUSH-02 denied, two Not nows, a snooze or an existing subscription keep it away [FR-42]", () => {
    expect(pushOfferable(device({ permission: "denied" }), memory(), NOW)).toBe(false);
    expect(pushOfferable(device(), memory({ pushDenied: true }), NOW, true)).toBe(false);
    expect(pushOfferable(device(), memory({ n1NotNow: 2 }), NOW)).toBe(false);
    expect(pushOfferable(device(), memory({ n1NotNow: 1, n1SnoozedUntil: NOW + 1 }), NOW)).toBe(
      false,
    );
    expect(pushOfferable(device(), memory({ n1NotNow: 1, n1SnoozedUntil: NOW - 1 }), NOW)).toBe(
      true,
    );
    expect(pushOfferable(device({ permission: "granted", subscribed: true }), memory(), NOW)).toBe(
      false,
    );
    expect(decide("bill", device({ permission: "denied" }), memory(), NOW)).toBeNull();
  });

  it("PWA-05 the install card shows after the first bill or on the 2nd visit, at most twice [FR-42]", () => {
    const can = device({ canPrompt: true, permission: "denied" });
    expect(decide("visit", can, memory(), NOW)).toEqual({ kind: "install" });
    expect(decide("bill", can, memory({ installShown: 1 }), NOW)).toEqual({ kind: "install" });
    expect(decide("visit", can, memory({ installShown: 2 }), NOW)).toBeNull();
    expect(decide("visit", device({ canPrompt: false }), memory(), NOW)).toBeNull();
    expect(
      decide("visit", device({ canPrompt: true, standalone: true }), memory(), NOW),
    ).toBeNull();
    // Push first at the bill moment; the card waits for the next moment.
    expect(decide("bill", device({ canPrompt: true }), memory(), NOW)).toMatchObject({
      kind: "n1",
    });
  });

  it("PWA-06 an iPhone tab gets N1 with the install steps instead of a push prompt [FR-42]", () => {
    const iosTab = device({ ios: true, pushSupported: false, permission: "unsupported" });
    expect(decide("visit", iosTab, memory(), NOW)).toEqual({
      kind: "n1",
      platform: "ios",
      countsAsInstall: true,
    });
    expect(decide("ask", iosTab, memory(), NOW)).toEqual({
      kind: "n1",
      platform: "ios",
      countsAsInstall: false,
    });
    expect(decide("visit", iosTab, memory({ installShown: 2 }), NOW)).toBeNull();
    // From the Home Screen, push is offered again on the first standalone launch.
    expect(
      decide("standalone", device({ ios: true, standalone: true }), memory(), NOW),
    ).toMatchObject({ kind: "n1", platform: "ios" });
  });
});
