import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { authorizeBlobUpload, BlobRefusal } from "@/lib/blob/authorize";
import { blobProblem, blobToken } from "@/lib/blob/route-utils";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * `POST /api/blob-upload` (ADR-0015): `@vercel/blob/client` `upload()` asks here for a client
 * token. The policy decision is the API's: the browser's cookie and CSRF header go with the pathname
 * to `POST /api/v1/uploads/authorize`; only when it answers 200 is a token minted, scoped to that
 * pathname, the authorized type and exactly the authorized size, valid until the ticket expires.
 * Upload-completed webhooks are not used (the client attaches the key itself), so only token
 * requests are accepted. Only with STORAGE_PROVIDER=vercel_blob.
 */
export async function POST(request: Request): Promise<Response> {
  const token = blobToken();
  if (!token) return blobProblem(404, "NOT_FOUND", "Not here");
  let body: HandleUploadBody;
  try {
    body = (await request.json()) as HandleUploadBody;
  } catch {
    return blobProblem(400, "VALIDATION_FAILED", "Bad request");
  }
  if (body?.type !== "blob.generate-client-token") {
    return blobProblem(400, "VALIDATION_FAILED", "Only client tokens are handed out here");
  }
  try {
    const result = await handleUpload({
      token,
      request,
      body,
      onBeforeGenerateToken: (pathname, clientPayload) =>
        authorizeBlobUpload(request, pathname, clientPayload),
    });
    return Response.json(result, { headers: { "Cache-Control": "private, no-store" } });
  } catch (error) {
    if (error instanceof BlobRefusal)
      return Response.json(error.body, {
        status: error.status,
        headers: { "Content-Type": "application/problem+json" },
      });
    return blobProblem(503, "INTERNAL", "Couldn't reach file storage");
  }
}
