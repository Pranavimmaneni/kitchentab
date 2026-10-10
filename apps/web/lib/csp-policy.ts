/**
 * The Content-Security-Policy of every HTML page (ADR-0007 as amended, SEC-03). Pure and
 * edge-safe: middleware.ts calls it with a fresh nonce per request; Next.js puts that nonce on its
 * own inline and chunk scripts. `'strict-dynamic'` lets those scripts load the rest; the fixed
 * no-flash theme script is allowed by its hash. No `'unsafe-inline'` for scripts anywhere.
 */
export type CspOptions = {
  nonce: string;
  /** `'sha256-…'` of the theme script (lib/csp.ts THEME_SCRIPT_HASH). */
  themeHash: string;
  /** `next dev`: React Refresh needs eval and a websocket. */
  dev?: boolean;
  /** On Vercel: Blob origins and `upgrade-insecure-requests`; else the local storage origins. */
  deployed?: boolean;
};

/** Vercel Blob: the client SDK uploads through vercel.com; private blobs are served from here. */
export const VERCEL_STORAGE_ORIGINS = [
  "https://vercel.com",
  "https://*.blob.vercel-storage.com",
] as const;
/** Local storage: MinIO (:9000), the fake Blob (:4012) and the local-vercel Blob origin (:8444). */
export const LOCAL_STORAGE_ORIGINS = [
  "http://localhost:*",
  "http://127.0.0.1:*",
  "https://localhost:*",
] as const;

export function buildCsp({ nonce, themeHash, dev = false, deployed = false }: CspOptions): string {
  // Where uploads go and receipt images come from besides this origin.
  const storage = (deployed ? VERCEL_STORAGE_ORIGINS : LOCAL_STORAGE_ORIGINS).join(" ");
  const directives = [
    "default-src 'self'",
    // tesseract.js compiles its WebAssembly core: 'wasm-unsafe-eval' allows that and nothing else.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' ${themeHash} 'wasm-unsafe-eval'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: ${storage}`,
    "font-src 'self' data:",
    `connect-src 'self' ${storage}${dev ? " ws: wss:" : ""}`,
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  if (deployed) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}

/** A 128-bit nonce, base64 (Web Crypto: works in the edge runtime). */
export function newNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}

/** `'sha256-…'` of a script, with Web Crypto (lib/csp.ts does the same with node:crypto). */
export async function sha256SourceAsync(script: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(script));
  return `'sha256-${btoa(String.fromCharCode(...new Uint8Array(digest)))}'`;
}
