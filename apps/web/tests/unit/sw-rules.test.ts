import { describe, expect, it, vi } from "vitest";
import { markStale, networkFirst, type CacheLike } from "@/lib/pwa/network-first";
import {
  changesSignedInUser,
  isApiRead,
  isAppNavigation,
  isOcrAsset,
  neverCache,
  storable,
} from "@/lib/pwa/sw-rules";

const O = "https://kitchentab.test";
const req = (path: string, init: { method?: string; mode?: string; accept?: string; range?: string } = {}) => ({
  url: path.startsWith("http") ? path : `${O}${path}`,
  method: init.method ?? "GET",
  mode: init.mode ?? "cors",
  headers: new Headers({
    ...(init.accept ? { accept: init.accept } : {}),
    ...(init.range ? { range: init.range } : {}),
  }),
});

describe("service worker routing rules (PRD §20.1)", () => {
  it("PWA-03 API read models are network-first candidates [FR-50]", () => {
    for (const path of ["/api/v1/me", "/api/v1/houses/h1", "/api/v1/bills/b1", "/api/v1/houses/h1/kitchen", "/api/v1/me/inbox"]) {
      expect(isApiRead(req(path), O)).toBe(true);
    }
  });

  it("PWA-04 auth, mutations, SSE, receipt images, uploads, push and exports are never cached [FR-18]", () => {
    const never = [
      req("/api/v1/auth/csrf"),
      req("/api/v1/auth/otp/verify", { method: "POST" }),
      req("/api/v1/bills/b1/items", { method: "POST" }),
      req("/api/v1/bills/b1", { method: "PATCH" }),
      req("/api/v1/bills/b1/allocations", { method: "PUT" }),
      req("/api/v1/houses/h1/events"),
      req("/api/v1/houses/h1/events?after=12"),
      req("/api/v1/whatever", { accept: "text/event-stream" }),
      req("/api/v1/receipts/r1/view"),
      req("/api/v1/bills/b1/receipts"),
      req("/api/v1/uploads/authorize", { method: "POST" }),
      req("/api/blob-view?pathname=receipts/x.jpg&expires=1&sig=a"),
      req("/api/blob-upload", { method: "POST" }),
      req("/api/v1/push/subscriptions"),
      req("/api/v1/me/export"),
      req("/api/v1/houses/h1/tabs/export.csv"),
      req("/api/test/inbox"),
      req("http://localhost:9000/kitchentab-receipts/receipts/b/x.jpg"),
      req("https://store.private.blob.vercel-storage.com/receipts/b/x.jpg"),
      req("/ocr/lang/eng.traineddata.gz", { range: "bytes=0-10" }),
    ];
    for (const r of never) {
      expect(neverCache(r, O), r.url).toBe(true);
      expect(isApiRead(r, O), r.url).toBe(false);
      expect(isAppNavigation({ ...r, mode: "navigate" }, O), r.url).toBe(false);
    }
  });

  it("PWA-02 app screens are cached for offline, link pages with tokens are not [FR-50]", () => {
    expect(isAppNavigation(req("/", { mode: "navigate" }), O)).toBe(true);
    expect(isAppNavigation(req("/tabs", { mode: "navigate" }), O)).toBe(true);
    expect(isAppNavigation(req("/bills/b1", { mode: "navigate" }), O)).toBe(true);
    expect(isAppNavigation(req("/tabs"), O)).toBe(false); // RSC fetch, not a navigation
    for (const p of ["/b/tok", "/h/tok", "/g/tok", "/api/v1/me", "/dev/components"]) {
      expect(isAppNavigation(req(p, { mode: "navigate" }), O)).toBe(false);
    }
    expect(isOcrAsset(req("/ocr/core/tesseract-core-simd-lstm.wasm.js"), O)).toBe(true);
  });

  it("PWA-04 signing in, out or deleting the account empties this device's saved screens [FR-01]", () => {
    expect(changesSignedInUser(req("/api/v1/auth/otp/verify", { method: "POST" }), O)).toBe(true);
    expect(changesSignedInUser(req("/api/v1/auth/logout", { method: "POST" }), O)).toBe(true);
    expect(changesSignedInUser(req("/api/v1/me", { method: "DELETE" }), O)).toBe(true);
    expect(changesSignedInUser(req("/api/v1/auth/csrf"), O)).toBe(false);
    expect(changesSignedInUser(req("/api/v1/me", { method: "PATCH" }), O)).toBe(false);
  });

  it("PWA-04 private responses and non-200s are never stored; pages need HTML, the API JSON [FR-18]", () => {
    const res = (status: number, headers: Record<string, string>) => new Response("{}", { status, headers });
    expect(storable(res(200, { "content-type": "application/json", "cache-control": "no-store" }), "api")).toBe(true);
    expect(storable(res(200, { "content-type": "application/json", "cache-control": "private, no-store" }), "api")).toBe(false);
    expect(storable(res(404, { "content-type": "application/json" }), "api")).toBe(false);
    expect(storable(res(200, { "content-type": "text/html" }), "api")).toBe(false);
    expect(storable(res(200, { "content-type": "text/html; charset=utf-8", "cache-control": "private, no-store" }), "page")).toBe(true);
    expect(storable(res(200, { "content-type": "application/json", vary: "*" }), "api")).toBe(false);
  });
});

function memoryCache(): CacheLike & { store: Map<string, Response> } {
  const store = new Map<string, Response>();
  return {
    store,
    async match(r) {
      const hit = store.get(r.url);
      return hit?.clone();
    },
    async put(r, res) {
      store.set(r.url, res);
    },
  };
}

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

describe("network-first with a 3 s timeout (PWA-03)", () => {
  it("PWA-03 a fast network answer is used and saved [FR-50]", async () => {
    const cache = memoryCache();
    const res = await networkFirst({ request: new Request(`${O}/api/v1/me`), cache, fetch: async () => json({ n: 1 }), kind: "api" });
    expect(await res.json()).toEqual({ n: 1 });
    expect(res.headers.get("x-kt-stale")).toBeNull();
    expect(cache.store.has(`${O}/api/v1/me`)).toBe(true);
    expect(cache.store.get(`${O}/api/v1/me`)!.headers.get("x-kt-cached-at")).toMatch(/^\d+$/);
  });

  it("PWA-03 after 3 s the saved copy is served marked stale, and refreshed in the background [FR-50]", async () => {
    vi.useFakeTimers();
    try {
      const cache = memoryCache();
      await cache.put(new Request(`${O}/api/v1/me`), json({ n: "old" }));
      let release!: (r: Response) => void;
      const slow = new Promise<Response>((resolve) => (release = resolve));
      const background: Promise<unknown>[] = [];
      const pending = networkFirst({
        request: new Request(`${O}/api/v1/me`),
        cache,
        fetch: () => slow,
        kind: "api",
        waitUntil: (p) => background.push(p),
      });
      await vi.advanceTimersByTimeAsync(2999);
      let done = false;
      void pending.then(() => (done = true));
      await Promise.resolve();
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      const res = await pending;
      expect(res.headers.get("x-kt-stale")).toBe("1");
      expect(await res.json()).toEqual({ n: "old" });
      release(json({ n: "new" }));
      await Promise.all(background);
      expect(await (await cache.match(new Request(`${O}/api/v1/me`)))!.json()).toEqual({ n: "new" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("OFFLINE-01 offline: the saved copy marked stale; nothing saved → the offline page; else an error [FR-50]", async () => {
    const cache = memoryCache();
    const offline = async () => {
      throw new TypeError("Failed to fetch");
    };
    await cache.put(new Request(`${O}/tabs`), new Response("<p>tabs</p>", { headers: { "content-type": "text/html" } }));
    const saved = await networkFirst({ request: new Request(`${O}/tabs`), cache, fetch: offline, kind: "page" });
    expect(saved.headers.get("x-kt-stale")).toBe("1");
    expect(await saved.text()).toBe("<p>tabs</p>");
    const fallback = await networkFirst({
      request: new Request(`${O}/house`),
      cache,
      fetch: offline,
      kind: "page",
      fallback: async () => new Response("offline page"),
    });
    expect(await fallback.text()).toBe("offline page");
    const none = await networkFirst({ request: new Request(`${O}/api/v1/x`), cache, fetch: offline, kind: "api" });
    expect(none.type).toBe("error");
  });

  it("PWA-03 a slow network with nothing saved still waits for the network [FR-50]", async () => {
    vi.useFakeTimers();
    try {
      const cache = memoryCache();
      const pending = networkFirst({
        request: new Request(`${O}/api/v1/me`),
        cache,
        fetch: () => new Promise((resolve) => setTimeout(() => resolve(json({ late: true })), 5000)),
        kind: "api",
      });
      await vi.advanceTimersByTimeAsync(5000);
      const res = await pending;
      expect(await res.json()).toEqual({ late: true });
      expect(markStale(json({})).headers.get("x-kt-stale")).toBe("1");
    } finally {
      vi.useRealTimers();
    }
  });
});
