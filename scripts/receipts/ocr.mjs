// OCR the image fixtures the way the browser will (BUILD_ALL C1 step 2), writing <id>.ocr.json.
//
//   npm i --prefix .cache/ocr-tools tesseract.js@5 @tesseract.js-data/eng@1 pdfjs-dist@4   (once)
//   node scripts/receipts/ocr.mjs [id ...]
//
// Pages of image-only PDFs are rendered with pdfjs-dist and every image is preprocessed by
// preprocess.js inside Playwright's Chromium (canvas, the same code path as the web app), then
// recognized with tesseract.js v5 (LSTM, eng best_int, PSM 6 single uniform block, interword spaces kept).
// Output lines carry boxes and confidences in the preprocessed image's pixel space, matching the
// payload the web client posts to POST /bills/{id}/ocr (see app/extraction/lines.py:lines_from_ocr).
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..', '..');
const fixtures = path.join(repo, 'packages', 'fixtures', 'receipts');
const tools = path.join(repo, '.cache', 'ocr-tools', 'node_modules');
const debugDir = path.join(repo, '.cache', 'ocr-debug');
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.join(repo, '.cache', 'ms-playwright');

const webRequire = createRequire(path.join(repo, 'apps', 'web', 'package.json'));
const toolRequire = createRequire(path.join(tools, 'noop.js'));
const { chromium } = webRequire('@playwright/test');
const Tesseract = toolRequire('tesseract.js');
const TESS_VERSION = JSON.parse(readFileSync(path.join(tools, 'tesseract.js', 'package.json'), 'utf8')).version;
const LANG_DIR = path.join(tools, '@tesseract.js-data', 'eng', '4.0.0_best_int');
const PDFJS = path.join(tools, 'pdfjs-dist', 'build');

const manifest = JSON.parse(readFileSync(path.join(fixtures, 'manifest.json'), 'utf8'));
const only = new Set(process.argv.slice(2));
const targets = manifest.fixtures.filter((f) => f.kind !== 'text_pdf' && (!only.size || only.has(f.id)));
mkdirSync(debugDir, { recursive: true });

const ORIGIN = 'http://kt-ocr.local';
const MIME = { '.mjs': 'text/javascript', '.js': 'text/javascript', '.pdf': 'application/pdf', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.html': 'text/html' };

const browser = await chromium.launch();
const page = await browser.newPage();
await page.route(`${ORIGIN}/**`, async (route) => {
  const url = new URL(route.request().url());
  let file;
  if (url.pathname === '/') file = null;
  else if (url.pathname.startsWith('/pdfjs/')) file = path.join(PDFJS, url.pathname.slice(7));
  else if (url.pathname.startsWith('/fx/')) file = path.join(fixtures, decodeURIComponent(url.pathname.slice(4)));
  else if (url.pathname === '/preprocess.js') file = path.join(here, 'preprocess.js');
  if (file === null) return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>ocr</title>' });
  if (!file || !existsSync(file)) return route.fulfill({ status: 404, body: 'nope' });
  return route.fulfill({ contentType: MIME[path.extname(file)] || 'application/octet-stream', body: readFileSync(file) });
});
await page.goto(`${ORIGIN}/`);
await page.addScriptTag({ url: `${ORIGIN}/preprocess.js` });

const worker = await Tesseract.createWorker('eng', Tesseract.OEM.LSTM_ONLY, {
  langPath: LANG_DIR,
  cachePath: path.join(repo, '.cache', 'ocr-tools', 'tess-cache'),
  gzip: true,
});
await worker.setParameters({
  tessedit_pageseg_mode: Tesseract.PSM.SINGLE_BLOCK, // PSM 4 drops the right-hand price column on receipts
  preserve_interword_spaces: '1',
  user_defined_dpi: '300',
});

async function preprocessFile(file) {
  return page.evaluate(async ({ url, isPdf }) => {
    const sources = [];
    if (isPdf) {
      const pdfjs = await import('/pdfjs/pdf.min.mjs');
      pdfjs.GlobalWorkerOptions.workerSrc = '/pdfjs/pdf.worker.min.mjs';
      const doc = await pdfjs.getDocument(url).promise;
      for (let p = 1; p <= Math.min(doc.numPages, 10); p++) {
        const pg = await doc.getPage(p);
        const vp1 = pg.getViewport({ scale: 1 });
        const vp = pg.getViewport({ scale: 2000 / Math.max(vp1.width, vp1.height) });
        const c = document.createElement('canvas');
        c.width = Math.round(vp.width); c.height = Math.round(vp.height);
        await pg.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise;
        sources.push(c);
      }
    } else {
      const blob = await (await fetch(url)).blob();
      sources.push(await createImageBitmap(blob, { imageOrientation: 'from-image' }));
    }
    return sources.map((s) => {
      const r = window.ktPreprocess(s);
      return { png: r.canvas.toDataURL('image/png'), angle: r.angle, scale: r.scale, width: r.width, height: r.height };
    });
  }, { url: `/fx/${encodeURIComponent(file)}`, isPdf: file.endsWith('.pdf') });
}

function linesOf(data) {
  const out = [];
  for (const block of data.blocks || []) {
    for (const para of block.paragraphs || []) {
      for (const line of para.lines || []) {
        const text = line.text.replace(/\n/g, ' ').trim();
        if (!text) continue;
        out.push({
          text,
          bbox: [line.bbox.x0, line.bbox.y0, line.bbox.x1, line.bbox.y1],
          confidence: Math.round(line.confidence * 10) / 10,
          words: (line.words || []).map((w) => ({
            text: w.text, bbox: [w.bbox.x0, w.bbox.y0, w.bbox.x1, w.bbox.y1], confidence: Math.round(w.confidence * 10) / 10,
          })),
        });
      }
    }
  }
  return out;
}

try {
  for (const fx of targets) {
    const pages = [];
    for (const file of fx.files) {
      const prepped = await preprocessFile(file);
      for (const pp of prepped) {
        const buf = Buffer.from(pp.png.split(',')[1], 'base64');
        const n = pages.length + 1;
        writeFileSync(path.join(debugDir, `${fx.id}_${n}.png`), buf);
        const { data } = await worker.recognize(buf, {}, { blocks: true, text: false });
        pages.push({
          page: n, file, width: pp.width, height: pp.height,
          preprocess: { deskew_deg: Math.round(pp.angle * 100) / 100, scale: Math.round(pp.scale * 1000) / 1000 },
          lines: linesOf(data),
        });
      }
    }
    const out = {
      id: fx.id,
      engine: `tesseract.js ${TESS_VERSION} eng 4.0.0_best_int psm6`,
      preprocess: 'grayscale, contrast 1-99%, deskew, Bradley threshold t=0.15, max 2000 px',
      multi_photo: !!fx.multi_photo,
      pages,
    };
    writeFileSync(path.join(fixtures, `${fx.id}.ocr.json`), JSON.stringify(out, null, 1) + '\n');
    const nl = pages.reduce((a, p) => a + p.lines.length, 0);
    process.stdout.write(`${fx.id}: ${pages.length} page(s), ${nl} lines, deskew ${pages.map((p) => p.preprocess.deskew_deg).join('/')}\n`);
  }
} finally {
  await worker.terminate();
  await browser.close();
}
