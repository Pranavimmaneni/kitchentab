/**
 * In-browser OCR with tesseract.js 5 (PRD §9 step 2, ADR-0010): LSTM only, `eng` 4.0.0_best_int,
 * page segmentation 6 (one uniform block: PSM 4 drops the right-hand price column on receipts),
 * interword spaces kept, 300 dpi. Worker, core and model are served from /ocr/ on this origin and
 * the worker is loaded from its own URL, not a blob, so nothing is fetched from a CDN (EXTRACT-12).
 *
 * The lines and boxes go to `POST /bills/{id}/ocr` exactly as scripts/receipts/ocr.mjs wrote the
 * fixtures (`app/extraction/lines.py:lines_from_ocr` reads them).
 */
import { preprocess } from "./preprocess";

export const OCR_ASSETS = {
  worker: "/ocr/worker.min.js",
  core: "/ocr/core/",
  lang: "/ocr/lang",
} as const;

export type OcrWord = { text: string; bbox: [number, number, number, number]; confidence: number };
export type OcrLine = OcrWord & { words: OcrWord[] };
export type OcrPage = { page: number; width: number; height: number; lines: OcrLine[] };
export type OcrPart = { pages: OcrPage[] };

type Bbox = { x0: number; y0: number; x1: number; y1: number };
type TessWord = { text: string; bbox: Bbox; confidence: number };
type TessLine = { text: string; bbox: Bbox; confidence: number; words?: TessWord[] };
type TessBlock = { paragraphs?: { lines?: TessLine[] }[] };
export type TessData = { blocks?: TessBlock[] | null };

const round1 = (n: number) => Math.round(n * 10) / 10;
const box = (b: Bbox): [number, number, number, number] => [b.x0, b.y0, b.x1, b.y1];

/** tesseract's block tree → the API's flat line list (empty lines dropped). */
export function linesOf(data: TessData): OcrLine[] {
  const out: OcrLine[] = [];
  for (const block of data.blocks ?? []) {
    for (const para of block.paragraphs ?? []) {
      for (const line of para.lines ?? []) {
        const text = line.text.replace(/\n/g, " ").trim();
        if (!text) continue;
        out.push({
          text: text.slice(0, 500),
          bbox: box(line.bbox),
          confidence: round1(line.confidence),
          words: (line.words ?? []).slice(0, 200).map((w) => ({
            text: w.text.slice(0, 200),
            bbox: box(w.bbox),
            confidence: round1(w.confidence),
          })),
        });
      }
    }
  }
  return out.slice(0, 3000);
}

type Worker = {
  setParameters(p: Record<string, string>): Promise<unknown>;
  recognize(image: HTMLCanvasElement, options: object, output: object): Promise<{ data: TessData }>;
  terminate(): Promise<unknown>;
};

export type Ocr = {
  /** Preprocesses one page and reads it. */
  read(
    source: CanvasImageSource & { width: number; height: number },
    page: number,
  ): Promise<OcrPage>;
  close(): Promise<void>;
};

/** Starts a tesseract worker from /ocr/ (the model is cached in IndexedDB after the first run). */
export async function startOcr(onProgress?: (fraction: number) => void): Promise<Ocr> {
  const T = await import("tesseract.js");
  const worker = (await T.createWorker("eng", T.OEM.LSTM_ONLY, {
    workerPath: OCR_ASSETS.worker,
    corePath: OCR_ASSETS.core,
    langPath: OCR_ASSETS.lang,
    gzip: true,
    workerBlobURL: false,
    logger: (m: { status: string; progress: number }) => {
      if (m.status === "recognizing text") onProgress?.(m.progress);
    },
  })) as unknown as Worker;
  await worker.setParameters({
    tessedit_pageseg_mode: String(T.PSM.SINGLE_BLOCK),
    preserve_interword_spaces: "1",
    user_defined_dpi: "300",
  });
  return {
    async read(source, page) {
      const prepared = preprocess(source);
      const { data } = await worker.recognize(prepared.canvas, {}, { blocks: true, text: false });
      return { page, width: prepared.width, height: prepared.height, lines: linesOf(data) };
    },
    async close() {
      await worker.terminate();
    },
  };
}
