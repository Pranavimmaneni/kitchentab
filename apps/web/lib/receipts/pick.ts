"use client";

import { strings } from "@/lib/strings";
import { PDF, checkFiles, checkPages, isRefusal, type Checked } from "./files";
import { pdfPageCount } from "./prepare";

export type Picked = Checked & { pages?: number };

/**
 * Checks a pick on the device before anything is created or uploaded (EXTRACT-01): type by magic
 * bytes, 20 MB, photos or one PDF, and the PDF's page count (≤ 10). Returns the refusal text.
 */
export async function checkPick(files: readonly File[]): Promise<Picked[] | string> {
  const checked = await checkFiles(files);
  if (isRefusal(checked)) return checked.message;
  const out: Picked[] = [];
  for (const c of checked) {
    if (c.mime !== PDF) {
      out.push(c);
      continue;
    }
    let pages: number;
    try {
      pages = await pdfPageCount(c.file);
    } catch {
      return strings.scan.refusal.pdf;
    }
    const tooMany = checkPages(pages);
    if (tooMany) return tooMany.message;
    out.push({ ...c, pages });
  }
  return out;
}
