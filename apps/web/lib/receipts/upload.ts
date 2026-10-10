/**
 * Upload one receipt file straight from the browser to storage (PRD §9 step 1, ADR-0015,
 * ADR-0024): the API decides (`POST /uploads/authorize`, policy check) and returns a ticket, the
 * file goes to storage without passing through any function body, then `POST /bills/{id}/receipts`
 * attaches the stored key (a create: Idempotency-Key + X-Client-Mutation-Id).
 *
 * Tickets: `PUT` (MinIO presigned URL, or the fake Blob service) or `blob-client` (Vercel Blob:
 * `@vercel/blob/client` asks our `/api/blob-upload` route for a client token, which asks the API
 * to authorize that exact pathname first).
 */
import { api } from "@/lib/api/client";
import type { ReceiptMime } from "./files";

export type UploadTicket = {
  provider: string;
  storage_key: string;
  method: "PUT" | "blob-client";
  upload_url: string | null;
  headers: Record<string, string>;
  content_type: string;
  max_bytes: number;
  expires_at: string;
  blob: {
    pathname: string;
    handle_upload_url: string | null;
    client_token: string | null;
  } | null;
};

export type Extraction = "done" | "needs_ocr" | "failed" | "offered";

export type ReceiptRow = { id: string; bill_id: string; mime: string; bytes: number; page_index: number };

export type AttachOut = {
  receipt: ReceiptRow;
  extraction: Extraction;
  result: Record<string, unknown> | null;
  bill: { id: string; version: number; extraction_status: string | null };
};

export type OcrOut = { extraction: Extraction; result: Record<string, unknown> | null };

/** What the `/api/blob-upload` route receives as `clientPayload` (it re-authorizes with it). */
export type BlobClientPayload = { bill_id: string; mime: string; bytes: number; pages?: number };

export class UploadError extends Error {
  constructor(readonly status: number) {
    super(`upload failed (${status})`);
    this.name = "UploadError";
  }
}

export function authorize(billId: string, mime: ReceiptMime, bytes: number, pages?: number) {
  return api.post<UploadTicket>("/uploads/authorize", {
    bill_id: billId,
    mime,
    bytes,
    ...(pages ? { pages } : {}),
  });
}

async function putToStorage(ticket: UploadTicket, body: Blob): Promise<void> {
  if (!ticket.upload_url) throw new UploadError(0);
  let res: Response;
  try {
    // Cross-origin to storage: no cookies. Content-Length is the authorized size (signed for S3).
    res = await fetch(ticket.upload_url, { method: "PUT", headers: ticket.headers, body, credentials: "omit" });
  } catch {
    throw new UploadError(0);
  }
  if (!res.ok) throw new UploadError(res.status);
}

async function blobClientUpload(ticket: UploadTicket, billId: string, body: Blob, mime: ReceiptMime, pages?: number) {
  if (!ticket.blob?.handle_upload_url) throw new UploadError(0);
  const { upload } = await import("@vercel/blob/client");
  const payload: BlobClientPayload = { bill_id: billId, mime, bytes: body.size, ...(pages ? { pages } : {}) };
  await upload(ticket.blob.pathname, body, {
    access: "private",
    contentType: mime,
    handleUploadUrl: ticket.blob.handle_upload_url,
    clientPayload: JSON.stringify(payload),
    // The route is same-origin and cookie-authenticated, so it gets the CSRF double-submit too.
    headers: { "X-CSRF-Token": await api.csrfToken() },
  });
}

/** Authorize → upload; returns the stored key. Nothing is written to the bill yet. */
export async function uploadToStorage(
  billId: string,
  body: Blob,
  mime: ReceiptMime,
  pages?: number,
): Promise<string> {
  const ticket = await authorize(billId, mime, body.size, pages);
  if (ticket.method === "blob-client") await blobClientUpload(ticket, billId, body, mime, pages);
  else await putToStorage(ticket, body);
  return ticket.storage_key;
}

export function attach(billId: string, storageKey: string, mime: ReceiptMime, bytes: number, key: string) {
  return api.post<AttachOut>(
    `/bills/${billId}/receipts`,
    { storage_key: storageKey, mime, bytes },
    { idempotencyKey: key },
  );
}

export function postOcr(
  billId: string,
  receiptId: string | undefined,
  parts: { pages: unknown[] }[],
  key: string,
) {
  return api.post<OcrOut>(
    `/bills/${billId}/ocr`,
    { ...(receiptId ? { receipt_id: receiptId } : {}), parts },
    { idempotencyKey: key },
  );
}
