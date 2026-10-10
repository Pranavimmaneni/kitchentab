/**
 * The API the e2e suite runs against (TEST_PLAN §2): test inbox for email codes, fake SMS, test
 * routes on, its own database (`kitchentab_test`) and Redis db 1. The inbox secret is a fixed
 * test-only value; it guards nothing outside this local test server.
 */
import { createHash, X509Certificate } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * TARGET (Makefile, ADR-0017, ADR-0028): `local` (default) starts the API and web servers below;
 * `local-vercel` runs against the HTTPS rehearsal from `make local-vercel-up` (origin and inbox
 * secret from .cache/local-vercel/state.json); `prod` against KT_PROD_URL with TEST_INBOX_SECRET.
 * Both remote targets are one origin (the edge routes /api/* to the API) and sign up only
 * `@launch-check.kitchentab.test` addresses, which `make prod-clean-test-data` removes.
 */
export const TARGET = process.env.TARGET ?? "local";
export const REMOTE = TARGET === "local-vercel" || TARGET === "prod";
const LV_DIR = resolve(process.cwd(), "../../.cache/local-vercel");
type Rehearsal = { base_url: string; blob_url?: string; secrets: { TEST_INBOX_SECRET: string } };
function rehearsal(): Rehearsal {
  const file = process.env.KT_LV_STATE ?? resolve(LV_DIR, "state.json");
  if (!existsSync(file)) throw new Error("TARGET=local-vercel: run `make local-vercel-up` first");
  return JSON.parse(readFileSync(file, "utf8")) as Rehearsal;
}
const LV = TARGET === "local-vercel" ? rehearsal() : undefined;
const REMOTE_ORIGIN = LV?.base_url ?? (TARGET === "prod" ? (process.env.KT_PROD_URL ?? "") : "");
if (TARGET === "prod" && !REMOTE_ORIGIN.startsWith("https://"))
  throw new Error("TARGET=prod needs KT_PROD_URL");
/** Where receipt files live: the rehearsal's fake Blob origin, or Vercel Blob in production. */
export const BLOB_ORIGIN = LV?.blob_url ?? "";
/** The address domain for sign-ups (deploylib.TEST_EMAIL_DOMAIN on remote targets). */
export const EMAIL_DOMAIN = REMOTE ? "launch-check.kitchentab.test" : "example.com";

/**
 * Chromium trusts the rehearsal's certificate chain (Caddy's local CA) by key: the SPKI hashes of
 * the served leaf and intermediate. WebKit and Firefox can't take a CA without OS changes, so they
 * ignore certificate errors instead (ADR-0028). Node's own requests trust it via NODE_EXTRA_CA_CERTS.
 */
export function chromiumTrustArgs(): string[] {
  if (TARGET !== "local-vercel") return [];
  const certs = resolve(LV_DIR, "caddy/caddy");
  const files = [
    resolve(certs, "pki/authorities/local/intermediate.crt"),
    resolve(certs, "pki/authorities/local/root.crt"),
  ];
  const leaf = resolve(certs, `certificates/local/${new URL(REMOTE_ORIGIN).hostname}`);
  files.push(resolve(leaf, `${new URL(REMOTE_ORIGIN).hostname}.crt`));
  const spki = files
    .filter((f) => existsSync(f))
    .map((f) => {
      const der = new X509Certificate(readFileSync(f)).publicKey.export({
        type: "spki",
        format: "der",
      });
      return createHash("sha256").update(der).digest("base64");
    });
  return spki.length ? [`--ignore-certificate-errors-spki-list=${spki.join(",")}`] : [];
}

export const API_PORT = Number(process.env.KT_E2E_API_PORT ?? 8010);
export const API_ORIGIN = REMOTE ? REMOTE_ORIGIN : `http://localhost:${API_PORT}`;
/** The web server's port (override to run a second suite beside another one). */
export const WEB_PORT = Number(process.env.KT_E2E_WEB_PORT ?? 3000);
export const WEB_ORIGIN = REMOTE ? REMOTE_ORIGIN : `http://localhost:${WEB_PORT}`;
export const TEST_INBOX_SECRET =
  LV?.secrets.TEST_INBOX_SECRET ??
  (TARGET === "prod" ? (process.env.TEST_INBOX_SECRET ?? "") : undefined) ??
  process.env.KT_E2E_INBOX_SECRET ??
  "e2e-inbox-secret-0123456789abcdef0123456789abcdef0123456789abcde";

export const API_ENV: Record<string, string> = {
  APP_ENV: "test",
  TEST_ROUTES_ENABLED: "true",
  EMAIL_PROVIDER: "testinbox",
  TEST_INBOX_SECRET,
  SMS_PROVIDER: "fake",
  PROFILE: "local",
  // Overridable so a second suite can run beside the first (its own database and Redis db).
  DATABASE_URL:
    process.env.KT_E2E_DATABASE_URL ??
    "postgresql+asyncpg://kitchentab:kitchentab@localhost:5432/kitchentab_test",
  REDIS_URL: process.env.KT_E2E_REDIS_URL ?? "redis://localhost:6379/1",
  WEB_ORIGIN,
  PUBLIC_LINK_BASE: WEB_ORIGIN,
  // Every browser in the suite reads from one IP (localhost), so the per-IP read limit (300/min,
  // PRD §11) would throttle parallel workers. The limit itself is covered by the API tests.
  RATE_LIMIT_READS_PER_MIN: "5000",
  // Receipts go browser → the compose MinIO (presigned PUT); never Vercel Blob (ADR-0015).
  STORAGE_PROVIDER: "s3",
  S3_ENDPOINT: process.env.KT_E2E_S3_ENDPOINT ?? "http://localhost:9000",
  S3_BUCKET: "kitchentab-receipts",
  S3_ACCESS_KEY: "minioadmin",
  S3_SECRET_KEY: "minioadmin",
};
