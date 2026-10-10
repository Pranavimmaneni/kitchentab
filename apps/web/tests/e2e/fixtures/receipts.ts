import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { crc32, deflateSync } from "node:zlib";
import { expect, type Page } from "@playwright/test";
import { call } from "./app";

/** packages/fixtures/receipts (ADR-0020): generated receipts with ground truth. */
export const RECEIPTS = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../../../packages/fixtures/receipts",
);

export const receiptFile = (name: string) => path.join(RECEIPTS, name);

export type TruthItem = { name: string; quantity: string; line_total_cents: number };
export type Truth = { store_name: string; total_cents: number; items: TruthItem[] };

export function truth(id: string): Truth {
  return JSON.parse(readFileSync(receiptFile(`${id}.truth.json`), "utf8")) as Truth;
}

export type BillItemLike = {
  name: string;
  quantity: string;
  line_total_cents: number;
  discount_cents: number;
};

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

/**
 * Lines exactly right, as `app/extraction/evaluate.py` counts them: normalized name, gross line
 * total and quantity equal, matched in order (LCS), divided by max(truth, extracted).
 */
export function lineScore(
  items: BillItemLike[],
  expected: TruthItem[],
): { right: number; of: number } {
  const key = (name: string, cents: number, qty: string) => `${norm(name)}|${cents}|${Number(qty)}`;
  const a = items.map((i) => key(i.name, i.line_total_cents + i.discount_cents, i.quantity));
  const b = expected.map((i) => key(i.name, i.line_total_cents, i.quantity));
  const dp = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i]![j] = a[i] === b[j] ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!);
    }
  }
  return { right: dp[0]![0]!, of: Math.max(a.length, b.length) };
}

/** A white PNG (not a receipt): OCR finds nothing, the parser must not invent items. */
export function blankPng(width = 600, height = 800): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(td) >>> 0);
    return Buffer.concat([len, td, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 0; // grayscale
  const raw = Buffer.alloc((width + 1) * height, 0xff);
  for (let y = 0; y < height; y++) raw[y * (width + 1)] = 0; // filter: none
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** `jpeg` with an EXIF APP1 (GPS IFD with a latitude) right after SOI, like a phone photo. */
export function withExifGps(jpeg: Buffer): Buffer {
  const tiff = Buffer.from(
    "MM\0*\0\0\0\x08\0\x01\x88\x25\0\x04\0\0\0\x01\0\0\0\x1a\0\0\0\0GPSLatitude=40.7128N;GPSLongitude=74.0060W",
    "latin1",
  );
  const body = Buffer.concat([Buffer.from("Exif\0\0", "latin1"), tiff]);
  const header = Buffer.from([0xff, 0xe1, (body.length + 2) >> 8, (body.length + 2) & 0xff]);
  return Buffer.concat([jpeg.subarray(0, 2), header, body, jpeg.subarray(2)]);
}

/** A minimal, valid PDF with `pages` blank pages (for the 10-page limit). */
export function blankPdf(pages: number): Buffer {
  const objects: string[] = [];
  const kids = Array.from({ length: pages }, (_, i) => `${3 + i} 0 R`).join(" ");
  objects.push("<< /Type /Catalog /Pages 2 0 R >>");
  objects.push(`<< /Type /Pages /Kids [${kids}] /Count ${pages} >>`);
  for (let i = 0; i < pages; i++)
    objects.push("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 300] >>");
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((body, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const o of offsets) out += `${String(o).padStart(10, "0")} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

export type BillApi = {
  bill: {
    id: string;
    store_name: string | null;
    receipt_total_cents: number | null;
    extraction_status: string;
  };
  items: BillItemLike[];
  scanned_offer: { extraction_id: string; item_count: number } | null;
};

export function billOf(page: Page, id: string): Promise<BillApi> {
  return call<BillApi>(page, "GET", `/bills/${id}`);
}

export function billIdFromUrl(page: Page): string {
  const m = /\/bills\/([0-9a-f-]{36})\/review/.exec(page.url());
  expect(m, page.url()).not.toBeNull();
  return m![1]!;
}

/** Waits for the review screen to finish reading: the editable receipt, or the failure card. */
export async function waitForReading(page: Page, timeout = 200_000): Promise<"review" | "failed"> {
  await page.waitForURL(/\/bills\/[0-9a-f-]{36}\/review$/);
  const settled = page.locator('.kt-review, .kt-scan[data-phase="failed"]').first();
  await expect(settled).toBeVisible({ timeout });
  return (await page.locator('.kt-scan[data-phase="failed"]').count()) > 0 ? "failed" : "review";
}
