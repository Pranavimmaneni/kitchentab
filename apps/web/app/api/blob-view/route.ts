import { issueSignedToken, presignUrl } from "@vercel/blob";
import { VIEWABLE, verifyRead } from "@/lib/blob/sign";
import { VIEW_TTL_MS, blobProblem, blobToken } from "@/lib/blob/route-utils";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * `GET /api/blob-view?pathname=…&expires=…&sig=…` (ADR-0024, FR-18): the API already checked that
 * the viewer is in the bill's house and signed this link. Here: recompute the HMAC, refuse expired
 * or wrong links with 403, then mint a Vercel signed URL for exactly that blob and redirect to it.
 * The file is served from `*.blob.vercel-storage.com`, a separate origin (SEC-04). Only with
 * STORAGE_PROVIDER=vercel_blob; elsewhere the API hands out storage URLs itself.
 */
export async function GET(request: Request): Promise<Response> {
  const token = blobToken();
  if (!token) return blobProblem(404, "NOT_FOUND", "Not here");
  const url = new URL(request.url);
  const pathname = url.searchParams.get("pathname") ?? "";
  const check = verifyRead(
    token,
    pathname,
    url.searchParams.get("expires"),
    url.searchParams.get("sig"),
    Math.floor(Date.now() / 1000),
  );
  if (check !== "ok" || !VIEWABLE.test(pathname)) {
    return blobProblem(
      403,
      "FORBIDDEN",
      check === "expired" ? "This link expired" : "This link doesn't work",
    );
  }
  const validUntil = Math.min(
    Number(url.searchParams.get("expires")) * 1000,
    Date.now() + VIEW_TTL_MS,
  );
  try {
    const signed = await issueSignedToken({ token, pathname, operations: ["get"], validUntil });
    const { presignedUrl } = await presignUrl(signed, {
      operation: "get",
      pathname,
      validUntil,
      access: "private",
    });
    return new Response(null, {
      status: 302,
      headers: {
        Location: presignedUrl,
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
      },
    });
  } catch {
    return blobProblem(503, "INTERNAL", "Couldn't reach file storage");
  }
}
