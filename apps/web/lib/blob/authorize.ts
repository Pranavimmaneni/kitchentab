// Server-only: the policy half of `/api/blob-upload` (ADR-0015). Authorization lives in the API
// (`app/policy.py`); this only forwards the browser's request and turns the ticket into token limits.
import type { BlobClientPayload, UploadTicket } from "@/lib/receipts/upload";
import { apiOrigin, forwardHeaders } from "./route-utils";

/** The API said no (or the request was malformed): passed back to the browser as is. */
export class BlobRefusal extends Error {
  constructor(
    readonly status: number,
    readonly body: Record<string, unknown>,
  ) {
    super(typeof body.title === "string" ? body.title : `refused (${status})`);
    this.name = "BlobRefusal";
  }
}

function parsePayload(raw: string | null): BlobClientPayload {
  let value: unknown;
  try {
    value = raw ? JSON.parse(raw) : null;
  } catch {
    value = null;
  }
  const p = value as Partial<BlobClientPayload> | null;
  if (
    !p ||
    typeof p.bill_id !== "string" ||
    typeof p.mime !== "string" ||
    typeof p.bytes !== "number" ||
    !Number.isInteger(p.bytes) ||
    (p.pages !== undefined && (typeof p.pages !== "number" || !Number.isInteger(p.pages)))
  ) {
    throw new BlobRefusal(422, {
      title: "Bad upload request",
      status: 422,
      code: "VALIDATION_FAILED",
    });
  }
  return {
    bill_id: p.bill_id,
    mime: p.mime,
    bytes: p.bytes,
    ...(p.pages ? { pages: p.pages } : {}),
  };
}

export type TokenLimits = {
  allowedContentTypes: string[];
  maximumSizeInBytes: number;
  validUntil: number;
  addRandomSuffix: false;
  allowOverwrite: false;
};

/**
 * Asks the API to authorize exactly this pathname for this user (cookie + CSRF forwarded). The
 * token it leads to allows one content type, the authorized size and the ticket's lifetime.
 */
export async function authorizeBlobUpload(
  request: Request,
  pathname: string,
  clientPayload: string | null,
  fetchImpl: typeof fetch = fetch,
): Promise<TokenLimits> {
  const payload = parsePayload(clientPayload);
  let res: Response;
  try {
    res = await fetchImpl(`${apiOrigin(request)}/api/v1/uploads/authorize`, {
      method: "POST",
      headers: forwardHeaders(request),
      body: JSON.stringify({ ...payload, storage_key: pathname }),
      cache: "no-store",
    });
  } catch {
    throw new BlobRefusal(503, {
      title: "Couldn't reach the server",
      status: 503,
      code: "INTERNAL",
    });
  }
  if (!res.ok) {
    let body: Record<string, unknown> = { title: "Not allowed", status: res.status };
    try {
      body = (await res.json()) as Record<string, unknown>;
    } catch {
      // keep the generic body
    }
    throw new BlobRefusal(res.status, body);
  }
  const ticket = (await res.json()) as UploadTicket;
  if (ticket.storage_key !== pathname) {
    throw new BlobRefusal(422, {
      title: "That upload doesn't belong to this tab",
      status: 422,
      code: "VALIDATION_FAILED",
    });
  }
  return {
    allowedContentTypes: [ticket.content_type],
    maximumSizeInBytes: ticket.max_bytes,
    validUntil: Date.parse(ticket.expires_at),
    addRandomSuffix: false,
    allowOverwrite: false,
  };
}
