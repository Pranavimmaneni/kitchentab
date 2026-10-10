import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { buildCsp, newNonce, sha256SourceAsync } from "@/lib/csp-policy";
import { THEME_SCRIPT_HASH } from "@/lib/csp";
import { THEME_SCRIPT } from "@/lib/theme-script";
import { config, middleware } from "@/middleware";

function directives(csp: string): Map<string, string[]> {
  return new Map(
    csp.split(";").map((d) => {
      const [name, ...sources] = d.trim().split(/\s+/);
      return [name, sources] as [string, string[]];
    }),
  );
}

describe("content security policy", () => {
  it("SEC-03 scripts need the nonce or the theme hash: no unsafe-inline, no eval in production", async () => {
    const themeHash = await sha256SourceAsync(THEME_SCRIPT);
    expect(themeHash).toBe(THEME_SCRIPT_HASH); // Web Crypto and node:crypto agree
    const d = directives(buildCsp({ nonce: "abc", themeHash, deployed: true }));
    const scripts = d.get("script-src")!;
    expect(scripts).toEqual(
      expect.arrayContaining(["'nonce-abc'", "'strict-dynamic'", themeHash, "'wasm-unsafe-eval'"]),
    );
    expect(scripts).not.toContain("'unsafe-inline'");
    expect(scripts).not.toContain("'unsafe-eval'");
    expect(d.get("frame-ancestors")).toEqual(["'none'"]);
    expect(d.get("object-src")).toEqual(["'none'"]);
    expect(d.get("base-uri")).toEqual(["'self'"]);
    expect(d.has("upgrade-insecure-requests")).toBe(true);
    expect(d.get("connect-src")).toEqual([
      "'self'",
      "https://vercel.com",
      "https://*.blob.vercel-storage.com",
    ]);
  });

  it("SEC-03 local builds allow local storage and no HTTPS upgrade; dev adds eval for React Refresh", () => {
    const local = directives(buildCsp({ nonce: "n", themeHash: "'sha256-x'" }));
    expect(local.get("connect-src")).toContain("http://localhost:*");
    expect(local.has("upgrade-insecure-requests")).toBe(false);
    const dev = directives(buildCsp({ nonce: "n", themeHash: "'sha256-x'", dev: true }));
    expect(dev.get("script-src")).toContain("'unsafe-eval'");
    expect(dev.get("connect-src")).toContain("ws:");
  });

  it("SEC-03 every page gets a fresh nonce on the response and on the request Next renders with", async () => {
    const one = await middleware(new NextRequest("https://app.test/kitchen"));
    const two = await middleware(new NextRequest("https://app.test/kitchen"));
    const csp1 = one.headers.get("Content-Security-Policy")!;
    const csp2 = two.headers.get("Content-Security-Policy")!;
    expect(csp1).toMatch(/'nonce-[A-Za-z0-9+/=]{24}'/);
    expect(csp1).not.toBe(csp2);
    // NextResponse.next({ request: { headers } }) forwards overridden request headers like this:
    expect(one.headers.get("x-middleware-request-x-nonce")).toBe(/'nonce-([^']+)'/.exec(csp1)![1]);
    expect(newNonce()).not.toBe(newNonce());
  });

  it("SEC-03 the middleware covers pages, not /api, Next assets or static files", () => {
    const source = new RegExp(`^${config.matcher[0]!.source}$`);
    for (const page of ["/", "/kitchen", "/bills/123/review", "/b/tok", "/~offline"]) {
      expect(source.test(page), page).toBe(true);
    }
    for (const file of [
      "/api/v1/me",
      "/_next/static/chunks/a.js",
      "/sw.js",
      "/ocr/worker.min.js",
      "/icons/icon-192.png",
    ]) {
      expect(source.test(file), file).toBe(false);
    }
  });
});
