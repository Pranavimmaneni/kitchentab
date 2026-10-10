/**
 * Turns a picked file into what gets stored and what gets read (PRD §9 steps 1–2):
 *
 * - Photos are decoded by the browser with their EXIF rotation applied, then re-encoded through a
 *   canvas (JPEG 0.9, or PNG for PNGs). The stored original therefore has no EXIF, GPS or XMP
 *   (EXTRACT-02); `stripMetadata` runs on the result as a guard. HEIC is decoded by the browser
 *   itself (Safari does; elsewhere the photo is refused with a hint), so it needs no library (FR-10).
 * - PDFs are stored as they are; the server reads text PDFs, and scanned pages are rendered here
 *   with pdfjs-dist (self-hosted under /ocr/, never from a CDN: EXTRACT-12).
 */
import { MAX_SIDE } from "./preprocess";
import { stripMetadata } from "./exif";
import { PDF, type ReceiptMime } from "./files";

/** Longest side kept for the stored photo (a 12 MP phone photo stays sharp, the file stays small). */
export const STORE_MAX_SIDE = 4096;
export const PDFJS_URL = "/ocr/pdf.min.mjs";
export const PDFJS_WORKER_URL = "/ocr/pdf.worker.min.mjs";

export class PrepareError extends Error {
  constructor(
    readonly code: "decode" | "encode" | "pdf",
    message: string,
  ) {
    super(message);
    this.name = "PrepareError";
  }
}

export type Decoded = CanvasImageSource & { width: number; height: number; close?: () => void };

async function decodeWithImage(blob: Blob): Promise<Decoded> {
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    img.decoding = "async";
    img.src = url;
    await img.decode();
    // <img> applies EXIF orientation (CSS image-orientation: from-image is the default).
    const c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    c.getContext("2d")?.drawImage(img, 0, 0);
    return c;
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** The photo as pixels, upright (EXIF rotation applied). */
export async function decodeImage(blob: Blob): Promise<Decoded> {
  try {
    return await createImageBitmap(blob, { imageOrientation: "from-image" });
  } catch {
    try {
      return await decodeWithImage(blob);
    } catch {
      throw new PrepareError("decode", "decode failed");
    }
  }
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) =>
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new PrepareError("encode", "encode failed"))),
      type,
      quality,
    ),
  );
}

export type PreparedPhoto = {
  /** What is uploaded: re-encoded, no metadata. */
  blob: Blob;
  mime: ReceiptMime;
  /** What OCR reads (the full-resolution upright photo). */
  source: Decoded;
};

/** Decodes, re-encodes without metadata, and keeps the pixels for OCR. */
export async function preparePhoto(file: Blob, mime: ReceiptMime): Promise<PreparedPhoto> {
  const source = await decodeImage(file);
  const scale = Math.min(1, STORE_MAX_SIDE / Math.max(source.width, source.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(source.width * scale));
  canvas.height = Math.max(1, Math.round(source.height * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new PrepareError("encode", "canvas 2d unavailable");
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  const outMime: ReceiptMime = mime === "image/png" ? "image/png" : "image/jpeg";
  const encoded = await toBlob(canvas, outMime, outMime === "image/jpeg" ? 0.9 : undefined);
  const clean = stripMetadata(new Uint8Array(await encoded.arrayBuffer()));
  return { blob: new Blob([clean as BlobPart], { type: outMime }), mime: outMime, source };
}

// ---- PDFs (pdfjs-dist, loaded at run time from /ocr/) ----------------------------------------------

type PdfPage = {
  getViewport(o: { scale: number }): { width: number; height: number };
  render(o: { canvasContext: CanvasRenderingContext2D; viewport: unknown }): {
    promise: Promise<void>;
  };
};
type PdfDoc = { numPages: number; getPage(n: number): Promise<PdfPage>; destroy(): Promise<void> };
type PdfJs = {
  GlobalWorkerOptions: { workerSrc: string };
  getDocument(src: { data: Uint8Array; isEvalSupported?: boolean }): { promise: Promise<PdfDoc> };
};

let pdfjs: Promise<PdfJs> | null = null;

/** pdfjs-dist from this origin (copied by scripts/copy-ocr-assets.mjs); no bundler, no CDN. */
export function loadPdfJs(): Promise<PdfJs> {
  pdfjs ??= (import(/* webpackIgnore: true */ PDFJS_URL) as Promise<PdfJs>).then((mod) => {
    mod.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
    return mod;
  });
  pdfjs.catch(() => {
    pdfjs = null;
  });
  return pdfjs;
}

async function openPdf(file: Blob): Promise<PdfDoc> {
  const lib = await loadPdfJs();
  // pdfjs transfers the buffer to its worker, so it gets its own copy.
  const data = new Uint8Array(await file.arrayBuffer());
  try {
    return await lib.getDocument({ data, isEvalSupported: false }).promise;
  } catch {
    throw new PrepareError("pdf", "pdf unreadable");
  }
}

export async function pdfPageCount(file: Blob): Promise<number> {
  const doc = await openPdf(file);
  try {
    return doc.numPages;
  } finally {
    void doc.destroy();
  }
}

/** Pages rendered at 2000 px on the long side (as scripts/receipts/ocr.mjs does). */
export async function renderPdfPages(file: Blob, maxPages: number): Promise<HTMLCanvasElement[]> {
  const doc = await openPdf(file);
  try {
    const out: HTMLCanvasElement[] = [];
    for (let n = 1; n <= Math.min(doc.numPages, maxPages); n++) {
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const viewport = page.getViewport({ scale: MAX_SIDE / Math.max(base.width, base.height) });
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(viewport.width);
      canvas.height = Math.round(viewport.height);
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new PrepareError("pdf", "canvas 2d unavailable");
      await page.render({ canvasContext: ctx, viewport }).promise;
      out.push(canvas);
    }
    return out;
  } finally {
    void doc.destroy();
  }
}

export function isPdf(mime: ReceiptMime): boolean {
  return mime === PDF;
}
