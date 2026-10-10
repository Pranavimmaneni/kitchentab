// Server-only helpers for the two Vercel Blob routes (ADR-0015, ADR-0024).
import { CLIENT_VERSION } from "@/lib/api/client";

/** Vercel signed URLs live as long as our own link: 5 minutes at most (ADR-0024). */
export const VIEW_TTL_MS = 5 * 60_000;

/** The routes exist only for Vercel Blob; with MinIO or the fake the API issues URLs itself. */
export function blobToken(): string | null {
  if (process.env.STORAGE_PROVIDER !== "vercel_blob") return null;
  return process.env.BLOB_READ_WRITE_TOKEN || null;
}

/** RFC 9457 problem details, the same shape as the API's errors. */
export function blobProblem(
  status: number,
  code: string,
  title: string,
  extra: Record<string, unknown> = {},
) {
  return Response.json(
    { type: "about:blank", title, status, code, ...extra },
    {
      status,
      headers: { "Content-Type": "application/problem+json", "Cache-Control": "private, no-store" },
    },
  );
}

/**
 * Where the API lives from this server's point of view: `KT_API_ORIGIN` locally (Next proxies
 * /api to uvicorn), else this request's own origin (Vercel Services route /api/v1 to FastAPI).
 */
export function apiOrigin(request: Request): string {
  return process.env.KT_API_ORIGIN || new URL(request.url).origin;
}

/** Calls the API as the signed-in browser: its cookies and its CSRF double-submit header. */
export function forwardHeaders(request: Request): Record<string, string> {
  const out: Record<string, string> = {
    Accept: "application/json, application/problem+json",
    "Content-Type": "application/json",
    "X-Client-Version": CLIENT_VERSION,
  };
  const cookie = request.headers.get("cookie");
  const csrf = request.headers.get("x-csrf-token");
  const origin = request.headers.get("origin");
  if (cookie) out.Cookie = cookie;
  if (csrf) out["X-CSRF-Token"] = csrf;
  if (origin) out.Origin = origin;
  return out;
}
