// Server-only: node:crypto.
import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * Short-lived receipt view links for Vercel Blob (ADR-0024). The API signs
 * `/api/blob-view?pathname=…&expires=…&sig=…` with HMAC-SHA256 over `"GET\n<pathname>\n<expires>"`
 * keyed with BLOB_READ_WRITE_TOKEN (`apps/api/app/adapters/storage.py: sign_read`); the web route
 * recomputes it here. Hex, lowercase, like Python's `hexdigest()`.
 */
export function signRead(readWriteToken: string, pathname: string, expires: number): string {
  return createHmac("sha256", readWriteToken).update(`GET\n${pathname}\n${expires}`, "utf8").digest("hex");
}

export type ReadCheck = "ok" | "expired" | "bad";

/** Checks a view link: expiry (unix seconds) first, then the signature in constant time. */
export function verifyRead(
  readWriteToken: string,
  pathname: string,
  expires: string | null,
  sig: string | null,
  nowSeconds: number,
): ReadCheck {
  if (!pathname || !expires || !sig || !/^\d{1,12}$/.test(expires) || !/^[0-9a-f]{64}$/.test(sig)) return "bad";
  const at = Number(expires);
  const expected = Buffer.from(signRead(readWriteToken, pathname, at), "hex");
  const given = Buffer.from(sig, "hex");
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return "bad";
  return at < nowSeconds ? "expired" : "ok";
}

/** Only receipt files are ever viewed through this route. */
export const VIEWABLE = /^receipts\/[0-9a-f-]{36}\/[A-Za-z0-9_-]{32,64}\.(?:jpg|png|heic|webp|pdf)$/;
