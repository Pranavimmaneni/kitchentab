// Server-only: reads request cookies and headers (next/headers fails in a client bundle).
import { cookies, headers } from "next/headers";
import { API_PREFIX, ApiError, CLIENT_VERSION, type ProblemBody } from "./client";

export const SESSION_COOKIE = "kt_session";

/** Where server components reach FastAPI: `KT_API_ORIGIN`, else this request's own origin. */
export async function apiOrigin(): Promise<string> {
  if (process.env.KT_API_ORIGIN) return process.env.KT_API_ORIGIN.replace(/\/$/, "");
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  return `${proto}://${host}`;
}

/** The public origin links are shared from (OG image URLs, metadataBase). */
export async function publicOrigin(): Promise<string> {
  if (process.env.PUBLIC_LINK_BASE) return process.env.PUBLIC_LINK_BASE.replace(/\/$/, "");
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  return `${proto}://${host}`;
}

export async function hasSessionCookie(): Promise<boolean> {
  return Boolean((await cookies()).get(SESSION_COOKIE)?.value);
}

/** GET from the API on the server, forwarding the visitor's cookies (session for `my_member`). */
export async function serverGet<T>(path: string, { forwardCookies = true } = {}): Promise<T> {
  const origin = await apiOrigin();
  const requestHeaders: Record<string, string> = {
    Accept: "application/json",
    "X-Client-Version": CLIENT_VERSION,
  };
  if (forwardCookies) {
    const jar = await cookies();
    const cookieHeader = jar
      .getAll()
      .map((c) => `${c.name}=${encodeURIComponent(c.value)}`)
      .join("; ");
    if (cookieHeader) requestHeaders.Cookie = cookieHeader;
  }
  let res: Response;
  try {
    res = await fetch(`${origin}${API_PREFIX}${path}`, { headers: requestHeaders, cache: "no-store" });
  } catch {
    throw new ApiError(0, { code: "NETWORK", title: "Something jammed. Try again." });
  }
  if (!res.ok) {
    let body: ProblemBody = {};
    try {
      body = (await res.json()) as ProblemBody;
    } catch {
      body = {};
    }
    throw new ApiError(res.status, { status: res.status, ...body });
  }
  return (await res.json()) as T;
}
