import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const calls: string[] = [];
const state = {
  attachExtraction: "needs_ocr" as string,
  ocrExtraction: "done" as string,
  failOcrOnce: false,
  failUploadOnce: false,
};

vi.mock("@/lib/receipts/prepare", () => ({
  PrepareError: class PrepareError extends Error {
    constructor(
      readonly code: string,
      message: string,
    ) {
      super(message);
    }
  },
  preparePhoto: vi.fn(async (file: Blob) => {
    calls.push("prepare");
    return { blob: new Blob([await file.arrayBuffer()], { type: "image/jpeg" }), mime: "image/jpeg", source: { width: 10, height: 10 } };
  }),
  renderPdfPages: vi.fn(async () => {
    calls.push("render");
    return [{ width: 10, height: 10 }];
  }),
}));

vi.mock("@/lib/receipts/upload", () => ({
  UploadError: class UploadError extends Error {},
  uploadToStorage: vi.fn(async () => {
    calls.push("upload");
    if (state.failUploadOnce) {
      state.failUploadOnce = false;
      throw new Error("network");
    }
    return `receipts/b/${calls.length}.jpg`;
  }),
  attach: vi.fn(async (_bill: string, _key: string, _mime: string, _bytes: number, idem: string) => {
    calls.push(`attach:${idem}`);
    return { receipt: { id: `r${calls.length}` }, extraction: state.attachExtraction, result: null, bill: {} };
  }),
  postOcr: vi.fn(async (_bill: string, receiptId: string | undefined, parts: unknown[], idem: string) => {
    calls.push(`ocr:${receiptId}:${parts.length}:${idem}`);
    return { extraction: state.ocrExtraction, result: null };
  }),
}));

vi.mock("@/lib/receipts/ocr", () => ({
  startOcr: vi.fn(async () => ({
    read: async (_source: unknown, page: number) => {
      calls.push(`read:${page}`);
      if (state.failOcrOnce) {
        state.failOcrOnce = false;
        throw new Error("wasm crashed");
      }
      return { page, width: 10, height: 10, lines: [] };
    },
    close: async () => undefined,
  })),
}));

const { getScanJob, resetScanJobs, retryScan, startScan, dismissScan } = await import("@/lib/receipts/scan-job");

const jpeg = () => ({ file: new File([new Uint8Array([0xff, 0xd8, 0xff, 1])], "r.jpg"), mime: "image/jpeg" as const });
const pdf = () => ({ file: new File(["%PDF-1.4"], "r.pdf"), mime: "application/pdf" as const, pages: 1 });

async function settled(billId: string) {
  await vi.waitFor(() => {
    const phase = getScanJob(billId)?.phase;
    expect(phase === "done" || phase === "failed").toBe(true);
  });
  return getScanJob(billId)!;
}

beforeEach(() => {
  calls.length = 0;
  Object.assign(state, { attachExtraction: "needs_ocr", ocrExtraction: "done", failOcrOnce: false, failUploadOnce: false });
  resetScanJobs();
});
afterEach(() => resetScanJobs());

describe("receipt scan job", () => {
  it("EXTRACT-12 photos: prepare → upload → attach → read on the device → POST /ocr, one part per photo [FR-10, FR-11]", async () => {
    startScan("b1", [jpeg(), jpeg()]);
    const job = await settled("b1");
    expect(job).toMatchObject({ phase: "done", outcome: "done", error: null });
    expect(calls.filter((c) => c === "upload")).toHaveLength(2);
    expect(calls.filter((c) => c.startsWith("read:"))).toHaveLength(2);
    const ocr = calls.find((c) => c.startsWith("ocr:"))!;
    expect(ocr.split(":")[2]).toBe("2"); // two parts
  });

  it("EXTRACT-11 a text PDF is read by the server at attach; nothing is OCR'd here [FR-10, FR-11]", async () => {
    state.attachExtraction = "done";
    startScan("b2", [pdf()]);
    expect(await settled("b2")).toMatchObject({ phase: "done", outcome: "done" });
    expect(calls.some((c) => c.startsWith("read:") || c.startsWith("ocr:"))).toBe(false);
  });

  it("SYNC-15 a scan that lands after hand-typed items is reported as offered [FR-14]", async () => {
    state.ocrExtraction = "offered";
    startScan("b3", [jpeg()]);
    expect(await settled("b3")).toMatchObject({ phase: "done", outcome: "offered" });
  });

  it("EXTRACT-06 an OCR failure keeps the file; Try again re-reads without uploading or attaching again [FR-10]", async () => {
    state.failOcrOnce = true;
    startScan("b4", [jpeg()]);
    const failed = await settled("b4");
    expect(failed).toMatchObject({ phase: "failed", error: "No items we could trust on it. The file is still attached." });
    const attachKey = calls.find((c) => c.startsWith("attach:"));
    calls.length = 0;
    expect(retryScan("b4")).toBe(true);
    expect(await settled("b4")).toMatchObject({ phase: "done" });
    expect(calls).not.toContain("upload");
    expect(calls.some((c) => c.startsWith("attach:"))).toBe(false);
    expect(calls.some((c) => c.startsWith("read:"))).toBe(true);
    expect(attachKey).toBeTruthy();
  });

  it("EXTRACT-06 an upload that fails is retried with the same attach key (no duplicate receipt) [FR-10]", async () => {
    state.failUploadOnce = true;
    startScan("b5", [jpeg()]);
    expect(await settled("b5")).toMatchObject({ phase: "failed" });
    retryScan("b5");
    expect(await settled("b5")).toMatchObject({ phase: "done" });
    expect(calls.filter((c) => c.startsWith("attach:"))).toHaveLength(1);
  });

  it("EXTRACT-06 the parser finding nothing is a failure: \"Couldn't read that one.\" [FR-10]", async () => {
    state.ocrExtraction = "failed";
    startScan("b6", [jpeg()]);
    expect(await settled("b6")).toMatchObject({ phase: "failed", outcome: "failed" });
    dismissScan("b6"); // Enter by hand
    expect(getScanJob("b6")).toBeNull();
  });

  it("EXTRACT-06 Enter by hand while reading keeps the scan going in the background [FR-15]", async () => {
    startScan("b7", [jpeg()]);
    dismissScan("b7");
    expect(getScanJob("b7")?.dismissed).toBe(true);
    expect(await settled("b7")).toMatchObject({ phase: "done", dismissed: true });
  });
});
