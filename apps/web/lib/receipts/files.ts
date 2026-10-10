/**
 * What a receipt file may be, checked on the device before anything is uploaded (PRD §5.2 FR-10,
 * §9 step 1; EXTRACT-01). The server checks the same rules again (`services/receipts.check_file`
 * and the magic-byte `sniff`); these only save a pointless upload and give the answer at once.
 */
import { strings } from "@/lib/strings";

export const MAX_BYTES = 20 * 1024 * 1024;
export const MAX_PDF_PAGES = 10;
/** One part per photo; the OCR payload takes at most 10 (apps/api/app/routes/receipts.py `Ocr`). */
export const MAX_PHOTOS = 10;
export const SNIFF_BYTES = 1024;

export type ReceiptMime = "image/jpeg" | "image/png" | "image/heic" | "image/webp" | "application/pdf";
export const PDF: ReceiptMime = "application/pdf";

/** `accept` for the photo library and the PDF picker. HEIC is listed so desktop pickers show it. */
export const ACCEPT_ANY = "image/*,.heic,.heif,application/pdf,.pdf";
export const ACCEPT_PDF = "application/pdf,.pdf";
export const ACCEPT_PHOTO = "image/*";

const HEIF_BRANDS = new Set(["heic", "heix", "hevc", "hevx", "heim", "heis", "mif1", "msf1"]);

function ascii(bytes: Uint8Array, start: number, end: number): string {
  let out = "";
  for (let i = start; i < Math.min(end, bytes.length); i++) out += String.fromCharCode(bytes[i]!);
  return out;
}

/** The real type from magic bytes, exactly like the server's `sniff` (never trust the name). */
export function sniffMime(head: Uint8Array): ReceiptMime | null {
  if (head.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return "image/jpeg";
  if (ascii(head, 0, 8) === "\x89PNG\r\n\x1a\n") return "image/png";
  if (ascii(head, 0, 4) === "RIFF" && ascii(head, 8, 12) === "WEBP") return "image/webp";
  if (ascii(head, 4, 8) === "ftyp" && HEIF_BRANDS.has(ascii(head, 8, 12))) return "image/heic";
  if (ascii(head, 0, SNIFF_BYTES).includes("%PDF-")) return PDF;
  return null;
}

export type Refusal = { code: "type" | "size" | "empty" | "pages" | "mixed" | "count"; message: string };
export type Checked = { file: File; mime: ReceiptMime };

export function isRefusal(value: unknown): value is Refusal {
  return typeof value === "object" && value !== null && "code" in value && "message" in value;
}

/** Size and type of one file from its first bytes (no decoding yet). */
export function checkBytes(head: Uint8Array, size: number): ReceiptMime | Refusal {
  const S = strings.scan.refusal;
  if (size <= 0) return { code: "empty", message: S.empty };
  const mime = sniffMime(head);
  if (mime === null) return { code: "type", message: S.type };
  if (size > MAX_BYTES) return { code: "size", message: S.size };
  return mime;
}

/** The PDF page limit (FR-10: 10 pages). */
export function checkPages(pages: number): Refusal | null {
  return pages > MAX_PDF_PAGES ? { code: "pages", message: strings.scan.refusal.pages } : null;
}

/**
 * A pick is either up to 10 photos (one long receipt in several shots) or one PDF. Mixed picks and
 * more than one PDF are refused rather than guessed at.
 */
export function checkSelection(mimes: readonly ReceiptMime[]): Refusal | null {
  const S = strings.scan.refusal;
  const pdfs = mimes.filter((m) => m === PDF).length;
  if (pdfs > 0 && mimes.length > 1) return { code: "mixed", message: S.mixed };
  if (mimes.length > MAX_PHOTOS) return { code: "count", message: S.count };
  return null;
}

async function head(file: Blob): Promise<Uint8Array> {
  return new Uint8Array(await file.slice(0, SNIFF_BYTES).arrayBuffer());
}

/** Checks every picked file; the first refusal wins. PDFs still need their page count checked. */
export async function checkFiles(files: readonly File[]): Promise<Checked[] | Refusal> {
  if (files.length === 0) return { code: "empty", message: strings.scan.refusal.empty };
  const out: Checked[] = [];
  for (const file of files) {
    const result = checkBytes(await head(file), file.size);
    if (isRefusal(result)) return result;
    out.push({ file, mime: result });
  }
  const mixed = checkSelection(out.map((c) => c.mime));
  return mixed ?? out;
}
