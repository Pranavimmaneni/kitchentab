// Server-only: imports node:crypto, so a client import fails the build.
import { createHash } from "node:crypto";
import { THEME_SCRIPT } from "./theme-script";

/** `sha256-…` source for the fixed no-flash theme script (ADR-0007). */
export function sha256Source(script: string): string {
  return `'sha256-${createHash("sha256").update(script, "utf8").digest("base64")}'`;
}

export const THEME_SCRIPT_HASH = sha256Source(THEME_SCRIPT);

/**
 * `script-src` for statically rendered pages. Milestone 10 adds the build-time hashes of the
 * Next.js flight scripts and enforces the header (SEC-03); until then this is only exported.
 */
export function scriptSrc(extraSources: readonly string[] = []): string {
  return ["script-src", "'self'", THEME_SCRIPT_HASH, ...extraSources].join(" ");
}
