/**
 * What the service worker caches (PRD §20.1, PWA-02..04, ADR-0027). Pure functions so the rules
 * are unit-tested; `app/sw.ts` wires them into Serwist.
 *
 *   precache        app shell (Next static chunks, CSS, fonts), icons, the offline page
 *   navigations     app screens, network-first (3 s) so a saved screen opens offline; link pages
 *                   (/b, /h, /g carry tokens) are never stored; no copy → the offline page
 *   API reads       GET /api/v1/* read models, network-first with a 3 s timeout, falling back to
 *                   the last copy marked stale (`x-kt-stale: 1`)
 *   never           auth, every write (the worker only routes GETs), the SSE stream and its
 *                   `?after=` catch-up, receipt images and their signed URLs, uploads, push,
 *                   exports, test and cron routes, anything cross-origin (storage, push services)
 *   /ocr/           the OCR worker, core and model: cache-first (large, versioned by build)
 */

export const CACHE_NAMES = {
  pages: "kt-pages-v1",
  api: "kt-api-v1",
  ocr: "kt-ocr-v1",
} as const;

/** The caches that hold one person's data; emptied whenever who is signed in changes. */
export const USER_CACHES = [CACHE_NAMES.pages, CACHE_NAMES.api] as const;

export const NETWORK_TIMEOUT_MS = 3000;
export const OFFLINE_URL = "/~offline";
export const STALE_HEADER = "x-kt-stale";
export const CACHED_AT_HEADER = "x-kt-cached-at";

const NEVER_API: readonly RegExp[] = [
  /^\/api\/v1\/auth(\/|$)/,
  /^\/api\/v1\/houses\/[^/]+\/events(\/|$)/, // SSE stream and ?after= catch-up: always live
  /^\/api\/v1\/receipts\//, // signed, short-lived receipt image URLs
  /^\/api\/v1\/bills\/[^/]+\/receipts(\/|$)/,
  /^\/api\/v1\/uploads\//,
  /^\/api\/v1\/push\//,
  /^\/api\/v1\/email\//,
  /^\/api\/v1\/cron\//,
  /^\/api\/v1\/queue\//,
  /^\/api\/v1\/__test\//,
  /\/export(\.csv)?(\/|$)/,
  /\.csv$/,
];

const NEVER_SAME_ORIGIN: readonly RegExp[] = [
  /^\/api\/test\//,
  /^\/api\/blob-(upload|view)(\/|$)/, // receipt images (Vercel Blob views) and upload tokens
  /^\/__test\//,
  /^\/sw[^/]*\.js$/,
  /^\/swe-worker/,
];

const LINK_PAGES = /^\/(b|h|g)\//;

export type RequestLike = {
  url: string;
  method: string;
  mode?: string;
  destination?: string;
  headers: { get(name: string): string | null };
};

function sameOrigin(url: URL, origin: string): boolean {
  return url.origin === origin;
}

/** Never put in any cache (checked before every other rule). */
export function neverCache(req: RequestLike, origin: string): boolean {
  const url = new URL(req.url);
  if (req.method !== "GET") return true;
  if (!sameOrigin(url, origin)) return true;
  if ((req.headers.get("accept") ?? "").includes("text/event-stream")) return true;
  if (req.headers.get("range")) return true;
  if (NEVER_SAME_ORIGIN.some((r) => r.test(url.pathname))) return true;
  if (url.pathname.startsWith("/api/v1/") && NEVER_API.some((r) => r.test(url.pathname))) return true;
  return false;
}

/** `GET /api/v1/*` read models: network-first, 3 s, stale fallback. */
export function isApiRead(req: RequestLike, origin: string): boolean {
  if (neverCache(req, origin)) return false;
  return new URL(req.url).pathname.startsWith("/api/v1/");
}

/** App screens (HTML navigations), not link pages, not the API, not RSC fetches. */
export function isAppNavigation(req: RequestLike, origin: string): boolean {
  if (neverCache(req, origin)) return false;
  if (req.mode !== "navigate") return false;
  const path = new URL(req.url).pathname;
  return !LINK_PAGES.test(path) && !path.startsWith("/api/") && !path.startsWith("/dev/");
}

export function isOcrAsset(req: RequestLike, origin: string): boolean {
  if (neverCache(req, origin)) return false;
  return new URL(req.url).pathname.startsWith("/ocr/");
}

/** A write that changes who is signed in (sign in, sign out, delete account): drop user caches. */
export function changesSignedInUser(req: RequestLike, origin: string): boolean {
  const url = new URL(req.url);
  if (!sameOrigin(url, origin) || req.method === "GET" || req.method === "HEAD") return false;
  if (url.pathname.startsWith("/api/v1/auth/")) return true;
  return req.method === "DELETE" && url.pathname === "/api/v1/me";
}

export type ResponseLike = {
  ok: boolean;
  status: number;
  type?: string;
  headers: { get(name: string): string | null };
};

/**
 * Worth keeping: a whole 200 from this origin. API routes that answer `Cache-Control: private`
 * (link views, receipt URLs) opt out explicitly; the API's default `no-store` is for shared HTTP
 * caches, not this device's own saved copy (ADR-0027). Pages: Next marks every dynamic screen
 * `private, no-store`, so for navigations only the path rules above decide.
 */
export function storable(res: ResponseLike, kind: "api" | "page"): boolean {
  if (!res.ok || res.status !== 200 || res.type === "opaque" || res.type === "opaqueredirect") return false;
  if (res.headers.get("vary")?.includes("*")) return false;
  if (kind === "page") return (res.headers.get("content-type") ?? "").includes("text/html");
  const cc = (res.headers.get("cache-control") ?? "").toLowerCase();
  if (cc.includes("private")) return false;
  return (res.headers.get("content-type") ?? "").includes("json");
}
