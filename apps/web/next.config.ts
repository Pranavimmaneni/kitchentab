import { randomUUID } from "node:crypto";
import path from "node:path";
import withSerwistInit from "@serwist/next";
import type { NextConfig } from "next";

// Same-origin API (ADR-0006): locally Next proxies /api/* to uvicorn so the browser only ever
// sees one origin. `next dev` always proxies; a production build proxies when KT_API_ORIGIN is
// set (the Playwright suite runs `next start` against a local API). On Vercel, Services route
// /api/* to FastAPI and KT_API_ORIGIN is unset.
const API_ORIGIN = process.env.KT_API_ORIGIN ?? "http://localhost:8000";
const PROXY_API = process.env.NODE_ENV === "development" || Boolean(process.env.KT_API_ORIGIN);

/**
 * The service worker (PRD §20.1, ADR-0027). Built from app/sw.ts into public/ as `/sw.js`; a side-by-
 * side local build (KT_DIST_DIR) writes its own `/sw-<dir>.js` so two suites never serve each
 * other's precache list. Off in `next dev`. The app registers it itself (lib/pwa/register.ts).
 */
const DIST_DIR = process.env.KT_DIST_DIR || ".next";
const BUILD_REVISION = process.env.VERCEL_GIT_COMMIT_SHA ?? randomUUID();
const SW_NAME =
  DIST_DIR === ".next" ? "sw" : `sw-${path.basename(DIST_DIR).replace(/[^A-Za-z0-9-]/g, "")}`;
const withSerwist = withSerwistInit({
  swSrc: "app/sw.ts",
  swDest: `public/${SW_NAME}.js`,
  swUrl: `/${SW_NAME}.js`,
  disable: process.env.NODE_ENV === "development",
  register: false,
  reloadOnOnline: false,
  cacheOnNavigation: false,
  // Nothing from public/: the OCR assets (~10 MB) are cached on first use, never precached; icons
  // and the manifest are route handlers, listed below with the offline page.
  globPublicPatterns: [],
  additionalPrecacheEntries: [
    "/~offline",
    "/manifest.webmanifest",
    "/icons/icon-192.png",
    "/icons/badge-96.png",
    "/icons/apple-touch-icon.png",
  ].map((url) => ({ url, revision: BUILD_REVISION })),
});

/**
 * Every page and file (SEC-03; the CSP itself is per request, in middleware.ts). /api is the FastAPI
 * service with its own headers. HSTS only on the production deployment: preview URLs and local
 * HTTPS rehearsals must not pin a browser to HTTPS for localhost.
 */
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(), geolocation=(), payment=(), usb=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(process.env.VERCEL_ENV === "production"
    ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }]
    : []),
];

/** Link pages never leak their token as a referrer and are never indexed (PRD §8, LINK-04). */
const LINK_HEADERS = [
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Robots-Tag", value: "noindex" },
];

const nextConfig: NextConfig = {
  // Lets two local suites build side by side (e.g. KT_DIST_DIR=test-results/next-alt, which lint
  // and tsc already ignore) without one `next build` wiping the other's running server.
  distDir: DIST_DIR,
  env: { NEXT_PUBLIC_KT_SW_URL: `/${SW_NAME}.js` },
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    if (!PROXY_API) return [];
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
  async headers() {
    return [
      // First, so the link pages' stricter Referrer-Policy below wins (the last match wins).
      { source: "/((?!api/).*)", headers: SECURITY_HEADERS },
      { source: "/b/:token*", headers: LINK_HEADERS },
      { source: "/h/:token*", headers: LINK_HEADERS },
      { source: "/g/:token*", headers: LINK_HEADERS },
      {
        // Updates are checked against the network every time (PWA-08).
        source: "/:sw(sw|sw-[A-Za-z0-9-]+).js",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
    ];
  },
};

export default withSerwist(nextConfig);
