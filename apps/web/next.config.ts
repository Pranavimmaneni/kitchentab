import type { NextConfig } from "next";

// Same-origin API (ADR-0006): locally Next proxies /api/* to uvicorn so the browser only ever
// sees one origin. `next dev` always proxies; a production build proxies when KT_API_ORIGIN is
// set (the Playwright suite runs `next start` against a local API). On Vercel, Services route
// /api/* to FastAPI and KT_API_ORIGIN is unset.
const API_ORIGIN = process.env.KT_API_ORIGIN ?? "http://localhost:8000";
const PROXY_API = process.env.NODE_ENV === "development" || Boolean(process.env.KT_API_ORIGIN);

/** Link pages never leak their token as a referrer and are never indexed (PRD §8, LINK-04). */
const LINK_HEADERS = [
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Robots-Tag", value: "noindex" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    if (!PROXY_API) return [];
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
  async headers() {
    return [
      { source: "/b/:token*", headers: LINK_HEADERS },
      { source: "/h/:token*", headers: LINK_HEADERS },
      { source: "/g/:token*", headers: LINK_HEADERS },
    ];
  },
};

export default nextConfig;
