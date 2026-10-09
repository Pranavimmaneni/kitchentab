import type { NextConfig } from "next";

// Same-origin API (ADR-0006): in local development `next dev` proxies /api/* to uvicorn so the
// browser only ever sees one origin. In production Vercel Services route /api/* to FastAPI.
const API_ORIGIN = process.env.KT_API_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
