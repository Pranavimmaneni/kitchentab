"use client";

/**
 * One receipt scan per draft, kept in memory across client-side navigation (capture sheet → review
 * screen), so the review screen can show "READING YOUR RECEIPT…", the failure state and Try again
 * (PRD §14A 2, EXTRACT-06). Steps, each resumable after a failure:
 *
 *   prepare  photos decoded upright and re-encoded without EXIF (EXTRACT-02)
 *   upload   authorize → straight to storage → attach (creates with Idempotency-Key)
 *   read     text PDFs are parsed by the API at attach; photos and scanned PDFs are OCR'd here
 *            (tesseract.js) and the lines posted to `POST /bills/{id}/ocr`
 *
 * The files stay on the job (and the stored copies stay attached on the server) after a failure,
 * so "Try again" never asks for them again. Nothing here writes to the cache: the review screen
 * refetches the bill when a job settles, and the server decides whether the scan is applied or
 * offered (SYNC-15).
 */
import { useSyncExternalStore } from "react";
import { isApiError, newId } from "@/lib/api/client";
import { strings } from "@/lib/strings";
import { MAX_BYTES, MAX_PDF_PAGES, PDF, type Checked } from "./files";
import { startOcr, type OcrPart } from "./ocr";
import { PrepareError, preparePhoto, renderPdfPages, type PreparedPhoto } from "./prepare";
import { UploadError, attach, postOcr, uploadToStorage, type Extraction } from "./upload";

export type ScanPhase = "preparing" | "uploading" | "reading" | "done" | "failed";

type Part = {
  checked: Checked;
  pages?: number;
  prepared?: PreparedPhoto;
  storageKey?: string;
  attachKey: string;
  receiptId?: string;
  extraction?: Extraction;
};

export type ScanJob = {
  readonly billId: string;
  readonly phase: ScanPhase;
  /** What the server did with the result: applied, offered (SYNC-15) or failed. */
  readonly outcome: Extraction | null;
  /** One line for the failure state. */
  readonly error: string | null;
  /** Reading progress, 0..1. */
  readonly progress: number;
  /** "Enter by hand" was chosen: the screen shows the editable draft while this keeps going. */
  readonly dismissed: boolean;
  readonly fileCount: number;
};

type Internal = {
  view: ScanJob;
  parts: Part[];
  ocrKey: string | null;
  run: number;
};

/** OCR gives up after this long ("Fail within 30 s" is for the server path; on-device is slower). */
export const OCR_TIMEOUT_MS = 120_000;

const jobs = new Map<string, Internal>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

function update(job: Internal, patch: Partial<ScanJob>): void {
  job.view = { ...job.view, ...patch };
  emit();
}

export function getScanJob(billId: string): ScanJob | null {
  return jobs.get(billId)?.view ?? null;
}

export function subscribeScanJobs(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useScanJob(billId: string): ScanJob | null {
  return useSyncExternalStore(
    subscribeScanJobs,
    () => getScanJob(billId),
    () => null,
  );
}

/** Starts scanning `files` into the draft `billId` (runs in the background). */
export function startScan(billId: string, files: readonly (Checked & { pages?: number })[]): void {
  const job: Internal = {
    view: {
      billId,
      phase: "preparing",
      outcome: null,
      error: null,
      progress: 0,
      dismissed: false,
      fileCount: files.length,
    },
    parts: files.map((f) => ({
      checked: { file: f.file, mime: f.mime },
      pages: f.pages,
      attachKey: newId(),
    })),
    ocrKey: null,
    run: 0,
  };
  jobs.set(billId, job);
  emit();
  void run(job);
}

/** Try again after a failure: resumes where it stopped, with the files it already has. */
export function retryScan(billId: string): boolean {
  const job = jobs.get(billId);
  if (!job || job.view.phase !== "failed") return false;
  job.ocrKey = null; // a new attempt is a new request
  update(job, { phase: "preparing", error: null, outcome: null, progress: 0, dismissed: false });
  void run(job);
  return true;
}

/** "Enter by hand": show the editable draft; a running scan keeps going and is offered later. */
export function dismissScan(billId: string): void {
  const job = jobs.get(billId);
  if (!job) return;
  if (job.view.phase === "failed" || job.view.phase === "done") jobs.delete(billId);
  else job.view = { ...job.view, dismissed: true };
  emit();
}

/** Forget a finished job (the review screen does this once it has refetched). */
export function clearScan(billId: string): void {
  if (jobs.delete(billId)) emit();
}

function failureText(error: unknown): string {
  const S = strings.scan;
  if (error instanceof PrepareError) return error.code === "pdf" ? S.refusal.pdf : S.refusal.decode;
  if (error instanceof UploadError) return S.uploadFailed;
  if (isApiError(error)) return error.status === 0 ? S.uploadFailed : error.title;
  if (error instanceof Error && error.message === "ocr timeout") return S.tooSlow;
  return S.notReadable;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("ocr timeout")), ms);
    promise.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e: unknown) => {
        clearTimeout(timer);
        reject(e instanceof Error ? e : new Error(String(e)));
      },
    );
  });
}

async function read(job: Internal): Promise<Extraction> {
  update(job, { phase: "reading", progress: 0 });
  const count = job.parts.length;
  const ocr = await startOcr((p) => update(job, { progress: Math.min(0.99, p / count) }));
  try {
    const parts: OcrPart[] = [];
    for (const part of job.parts) {
      if (part.checked.mime === PDF) {
        const canvases = await renderPdfPages(part.checked.file, MAX_PDF_PAGES);
        const pages = [];
        for (const [i, canvas] of canvases.entries()) pages.push(await ocr.read(canvas, i + 1));
        parts.push({ pages });
      } else {
        part.prepared ??= await preparePhoto(part.checked.file, part.checked.mime);
        parts.push({ pages: [await ocr.read(part.prepared.source, 1)] });
      }
    }
    job.ocrKey ??= newId();
    const out = await postOcr(job.view.billId, job.parts[0]?.receiptId, parts, job.ocrKey);
    return out.extraction;
  } finally {
    void ocr.close();
  }
}

async function run(job: Internal): Promise<void> {
  const generation = ++job.run;
  const { billId } = job.view;
  const live = () => job.run === generation && jobs.get(billId) === job;
  try {
    for (const part of job.parts) {
      if (part.checked.mime !== PDF && !part.prepared) {
        part.prepared = await preparePhoto(part.checked.file, part.checked.mime);
        if (part.prepared.blob.size > MAX_BYTES)
          throw new PrepareError("encode", "too large after encoding");
      }
    }
    if (!live()) return;
    update(job, { phase: "uploading" });
    for (const part of job.parts) {
      const body = part.prepared?.blob ?? part.checked.file;
      const mime = part.prepared?.mime ?? part.checked.mime;
      if (!part.storageKey) part.storageKey = await uploadToStorage(billId, body, mime, part.pages);
      if (!part.receiptId) {
        const out = await attach(billId, part.storageKey, mime, body.size, part.attachKey);
        part.receiptId = out.receipt.id;
        part.extraction = out.extraction;
      }
    }
    if (!live()) return;
    const results = job.parts.map((p) => p.extraction);
    let outcome: Extraction;
    if (results.some((r) => r === "done" || r === "offered")) {
      outcome = results.includes("offered") ? "offered" : "done";
    } else if (job.parts.every((p) => p.extraction === "failed") && generation === 1) {
      // A PDF the server read but couldn't parse: say so once; Try again reads it on the device.
      outcome = "failed";
    } else {
      outcome = await withTimeout(read(job), OCR_TIMEOUT_MS);
    }
    if (!live()) return;
    if (outcome === "failed") {
      update(job, { phase: "failed", outcome, error: strings.scan.notReadable, progress: 0 });
    } else {
      update(job, { phase: "done", outcome, error: null, progress: 1 });
    }
  } catch (error) {
    if (!live()) return;
    update(job, { phase: "failed", outcome: null, error: failureText(error), progress: 0 });
  }
}

/** Test seam: drop every job. */
export function resetScanJobs(): void {
  jobs.clear();
  emit();
}
