import { NextResponse, type NextRequest } from "next/server";
import { buildCsp, newNonce, sha256SourceAsync } from "@/lib/csp-policy";
import { THEME_SCRIPT } from "@/lib/theme-script";

/**
 * A fresh nonce and the CSP for every page (SEC-03, ADR-0007). Next.js reads the nonce from the
 * request's CSP header and stamps it on the scripts it renders; the root layout passes it to the
 * theme script. /api is the FastAPI service (it sets its own headers) and static files carry none.
 */
let themeHash: Promise<string> | undefined;

export async function middleware(request: NextRequest) {
  themeHash ??= sha256SourceAsync(THEME_SCRIPT);
  const nonce = newNonce();
  const csp = buildCsp({
    nonce,
    themeHash: await themeHash,
    dev: process.env.NODE_ENV === "development",
    deployed: process.env.VERCEL === "1",
  });
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      // Pages only: not /api, Next's assets, or files with an extension (icons, sw.js, /ocr/…).
      source: "/((?!api/|_next/static|_next/image|.*\\.[A-Za-z0-9]+$).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
