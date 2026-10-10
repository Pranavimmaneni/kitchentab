// Self-hosts the in-browser OCR under /ocr/ (BUILD_ALL C1, ADR-0010, EXTRACT-12: no third-party
// requests). Runs before `next dev` / `next build`; copies from the installed packages so versions
// follow the lockfile:
//
//   tesseract.js   dist/worker.min.js                      -> public/ocr/worker.min.js
//   tesseract.js-core  tesseract-core-{simd-,}lstm.wasm.js  -> public/ocr/core/  (LSTM only, OEM 1)
//   pdfjs-dist     build/pdf.min.mjs, pdf.worker.min.mjs    -> public/ocr/
//
// The `eng` model (4.0.0_best_int, the one the accuracy report was measured with) is committed at
// public/ocr/lang/eng.traineddata.gz; it is data, not a package (ADR-0026).
import { copyFileSync, existsSync, mkdirSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = path.join(web, "public", "ocr");
const require = createRequire(path.join(web, "package.json"));

const tesseractDir = path.dirname(require.resolve("tesseract.js/package.json"));
const coreDir = path.dirname(
  createRequire(path.join(tesseractDir, "package.json")).resolve("tesseract.js-core/package.json"),
);
const pdfjsDir = path.dirname(require.resolve("pdfjs-dist/package.json"));

const copies = [
  [path.join(tesseractDir, "dist", "worker.min.js"), path.join(out, "worker.min.js")],
  [
    path.join(coreDir, "tesseract-core-simd-lstm.wasm.js"),
    path.join(out, "core", "tesseract-core-simd-lstm.wasm.js"),
  ],
  [
    path.join(coreDir, "tesseract-core-lstm.wasm.js"),
    path.join(out, "core", "tesseract-core-lstm.wasm.js"),
  ],
  [path.join(pdfjsDir, "build", "pdf.min.mjs"), path.join(out, "pdf.min.mjs")],
  [path.join(pdfjsDir, "build", "pdf.worker.min.mjs"), path.join(out, "pdf.worker.min.mjs")],
];

mkdirSync(path.join(out, "core"), { recursive: true });
for (const [from, to] of copies) {
  if (!existsSync(from)) throw new Error(`missing OCR asset ${from}`);
  if (
    existsSync(to) &&
    statSync(to).size === statSync(from).size &&
    statSync(to).mtimeMs >= statSync(from).mtimeMs
  )
    continue;
  copyFileSync(from, to);
}
if (!existsSync(path.join(out, "lang", "eng.traineddata.gz"))) {
  throw new Error("public/ocr/lang/eng.traineddata.gz is missing (it is committed; see ADR-0026)");
}
