/**
 * Receipt image preprocessing before tesseract.js (PRD §9 step 2, ADR-0010): max 2000 px →
 * grayscale → contrast stretch (1st–99th percentile) → deskew (projection profile, ±10°) →
 * adaptive threshold (Bradley–Roth over an integral image). EXIF rotation happens earlier, when the
 * photo is decoded (`createImageBitmap(..., { imageOrientation: "from-image" })`).
 *
 * A typed copy of `scripts/receipts/preprocess.js`, which produced the `*.ocr.json` fixtures and the
 * accuracy numbers in packages/fixtures/receipts/REPORT.md; keep the two numerically identical. The
 * math is pure (unit-tested); only `preprocess()` touches canvases.
 */

export const MAX_SIDE = 2000;
const MIN_CONTRAST = 14;
const THRESHOLD_T = 0.15;

export function toGray(data: Uint8ClampedArray, w: number, h: number): Float32Array {
  const g = new Float32Array(w * h);
  for (let i = 0, j = 0; j < g.length; i += 4, j++) {
    g[j] = 0.299 * data[i]! + 0.587 * data[i + 1]! + 0.114 * data[i + 2]!;
  }
  return g;
}

function percentile(hist: Uint32Array, n: number, p: number): number {
  let acc = 0;
  for (let v = 0; v < 256; v++) {
    acc += hist[v]!;
    if (acc >= n * p) return v;
  }
  return 255;
}

/** Stretches the 1st..99th percentile to 0..255, in place. */
export function stretch(g: Float32Array): Float32Array {
  const hist = new Uint32Array(256);
  for (let i = 0; i < g.length; i++) hist[Math.max(0, Math.min(255, g[i]! | 0))]!++;
  const lo = percentile(hist, g.length, 0.01);
  const hi = percentile(hist, g.length, 0.99);
  const span = Math.max(hi - lo, 1);
  for (let i = 0; i < g.length; i++) g[i] = Math.max(0, Math.min(255, ((g[i]! - lo) * 255) / span));
  return g;
}

/** Bradley–Roth: ink (1) when darker than (1 - t) × the local mean and by more than 14 levels. */
export function threshold(g: Float32Array, w: number, h: number, win: number, t: number): Uint8Array {
  const integral = new Float64Array((w + 1) * (h + 1));
  for (let y = 0; y < h; y++) {
    let row = 0;
    for (let x = 0; x < w; x++) {
      row += g[y * w + x]!;
      integral[(y + 1) * (w + 1) + x + 1] = integral[y * (w + 1) + x + 1]! + row;
    }
  }
  const out = new Uint8Array(w * h);
  const r = win >> 1;
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - r);
    const y1 = Math.min(h - 1, y + r);
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - r);
      const x1 = Math.min(w - 1, x + r);
      const count = (x1 - x0 + 1) * (y1 - y0 + 1);
      const sum =
        integral[(y1 + 1) * (w + 1) + x1 + 1]! -
        integral[y0 * (w + 1) + x1 + 1]! -
        integral[(y1 + 1) * (w + 1) + x0]! +
        integral[y0 * (w + 1) + x0]!;
      const v = g[y * w + x]!;
      const mean = sum / count;
      out[y * w + x] = v < mean * (1 - t) && mean - v > MIN_CONTRAST ? 1 : 0;
    }
  }
  return out;
}

/** Skew in degrees: the angle whose horizontal projection profile of ink pixels is sharpest. */
export function estimateSkew(ink: Uint8Array, w: number, h: number): number {
  const pts: number[] = [];
  const step = Math.max(1, Math.floor(Math.sqrt((w * h) / 400000)));
  for (let y = 0; y < h; y += step) for (let x = 0; x < w; x += step) if (ink[y * w + x]) pts.push(x, y);
  if (pts.length < 200) return 0;
  const score = (deg: number): number => {
    const a = (deg * Math.PI) / 180;
    const s = Math.sin(a);
    const c = Math.cos(a);
    const bins = new Float64Array(Math.ceil(((w + h) * 2) / step) + 4);
    const off = w + 2;
    for (let i = 0; i < pts.length; i += 2) {
      bins[Math.round((-pts[i]! * s + pts[i + 1]! * c + off) / step)]!++;
    }
    let sc = 0;
    for (let i = 1; i < bins.length; i++) {
      const d = bins[i]! - bins[i - 1]!;
      sc += d * d;
    }
    return sc;
  };
  let best = 0;
  let bestScore = -1;
  for (let d = -10; d <= 10; d += 0.5) {
    const sc = score(d);
    if (sc > bestScore) {
      bestScore = sc;
      best = d;
    }
  }
  const coarse = best;
  for (let d = coarse - 0.5; d <= coarse + 0.5; d += 0.1) {
    const sc = score(d);
    if (sc > bestScore) {
      bestScore = sc;
      best = d;
    }
  }
  return best;
}

/** Odd window ≈ 1/40 of the long side, at least 15 px. */
export function windowFor(w: number, h: number): number {
  return Math.max(15, Math.round(Math.max(w, h) / 40)) | 1;
}

export type Prepared = { canvas: HTMLCanvasElement; angle: number; scale: number; width: number; height: number };

type Source = CanvasImageSource & { width: number; height: number };

function canvasOf(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
}

function context(c: HTMLCanvasElement): CanvasRenderingContext2D {
  const ctx = c.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("canvas 2d unavailable");
  return ctx;
}

function grayCanvas(g: Float32Array, w: number, h: number): HTMLCanvasElement {
  const c = canvasOf(w, h);
  const ctx = context(c);
  const img = ctx.createImageData(w, h);
  for (let i = 0, j = 0; j < g.length; i += 4, j++) {
    const v = g[j]! | 0;
    img.data[i] = v;
    img.data[i + 1] = v;
    img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/** The black-and-white, deskewed page tesseract reads. */
export function preprocess(source: Source, maxSide = MAX_SIDE): Prepared {
  const scale = Math.min(1, maxSide / Math.max(source.width, source.height));
  let w = Math.round(source.width * scale);
  let h = Math.round(source.height * scale);
  const c0 = canvasOf(w, h);
  const ctx0 = context(c0);
  ctx0.imageSmoothingQuality = "high";
  ctx0.drawImage(source, 0, 0, w, h);
  let g = stretch(toGray(ctx0.getImageData(0, 0, w, h).data, w, h));
  const win = windowFor(w, h);
  const angle = estimateSkew(threshold(g, w, h, win, THRESHOLD_T), w, h);
  if (Math.abs(angle) >= 0.2) {
    const a = (angle * Math.PI) / 180;
    const nw = Math.round(Math.abs(w * Math.cos(a)) + Math.abs(h * Math.sin(a)));
    const nh = Math.round(Math.abs(w * Math.sin(a)) + Math.abs(h * Math.cos(a)));
    const c1 = canvasOf(nw, nh);
    const ctx1 = context(c1);
    ctx1.fillStyle = "#fff";
    ctx1.fillRect(0, 0, nw, nh);
    ctx1.translate(nw / 2, nh / 2);
    ctx1.rotate(-a);
    ctx1.drawImage(grayCanvas(g, w, h), -w / 2, -h / 2);
    w = nw;
    h = nh;
    g = toGray(ctx1.getImageData(0, 0, w, h).data, w, h);
  }
  const bin = threshold(g, w, h, win, THRESHOLD_T);
  const out = new Float32Array(w * h);
  for (let i = 0; i < out.length; i++) out[i] = bin[i] ? 0 : 255;
  return { canvas: grayCanvas(out, w, h), angle, scale, width: w, height: h };
}
