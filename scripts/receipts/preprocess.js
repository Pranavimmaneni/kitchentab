// Receipt image preprocessing for OCR, as the browser runs it before tesseract.js (PRD §9 step 2):
// EXIF rotate (createImageBitmap imageOrientation) -> max 2000 px -> grayscale -> contrast stretch ->
// deskew (projection profile) -> adaptive threshold (Bradley, integral image).
// Plain browser JS with no dependencies so apps/web can reuse it in its OCR worker. ocr.mjs injects
// this file into Chromium and calls window.ktPreprocess(source) -> {canvas, angle, scale}.
(function () {
  const MAX_SIDE = 2000;
  const MIN_CONTRAST = 14;

  function toGray(ctx, w, h) {
    const { data } = ctx.getImageData(0, 0, w, h);
    const g = new Float32Array(w * h);
    for (let i = 0, j = 0; j < g.length; i += 4, j++) {
      g[j] = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    }
    return g;
  }

  function stretch(g) {
    const hist = new Uint32Array(256);
    for (let i = 0; i < g.length; i++) hist[Math.max(0, Math.min(255, g[i] | 0))]++;
    const lo = percentile(hist, g.length, 0.01);
    const hi = percentile(hist, g.length, 0.99);
    const span = Math.max(hi - lo, 1);
    for (let i = 0; i < g.length; i++) g[i] = Math.max(0, Math.min(255, ((g[i] - lo) * 255) / span));
    return g;
  }

  function percentile(hist, n, p) {
    let acc = 0;
    for (let v = 0; v < 256; v++) {
      acc += hist[v];
      if (acc >= n * p) return v;
    }
    return 255;
  }

  // Bradley-Roth adaptive threshold: a pixel is ink when darker than (1 - t) x its local mean.
  function threshold(g, w, h, win, t) {
    const integral = new Float64Array((w + 1) * (h + 1));
    for (let y = 0; y < h; y++) {
      let row = 0;
      for (let x = 0; x < w; x++) {
        row += g[y * w + x];
        integral[(y + 1) * (w + 1) + x + 1] = integral[y * (w + 1) + x + 1] + row;
      }
    }
    const out = new Uint8Array(w * h);
    const r = win >> 1;
    for (let y = 0; y < h; y++) {
      const y0 = Math.max(0, y - r), y1 = Math.min(h - 1, y + r);
      for (let x = 0; x < w; x++) {
        const x0 = Math.max(0, x - r), x1 = Math.min(w - 1, x + r);
        const count = (x1 - x0 + 1) * (y1 - y0 + 1);
        const sum = integral[(y1 + 1) * (w + 1) + x1 + 1] - integral[y0 * (w + 1) + x1 + 1]
          - integral[(y1 + 1) * (w + 1) + x0] + integral[y0 * (w + 1) + x0];
        const v = g[y * w + x], mean = sum / count;
        // Relative AND absolute contrast: flat dark areas (a table, a shadow) are not ink.
        out[y * w + x] = v < mean * (1 - t) && mean - v > MIN_CONTRAST ? 1 : 0;
      }
    }
    return out;
  }

  // Skew from ink pixels: the angle whose horizontal projection profile is sharpest.
  function estimateSkew(ink, w, h) {
    const pts = [];
    const step = Math.max(1, Math.floor(Math.sqrt((w * h) / 400000)));
    for (let y = 0; y < h; y += step) for (let x = 0; x < w; x += step) if (ink[y * w + x]) pts.push(x, y);
    if (pts.length < 200) return 0;
    const score = (deg) => {
      const a = (deg * Math.PI) / 180, s = Math.sin(a), c = Math.cos(a);
      const bins = new Float64Array(Math.ceil((w + h) * 2 / step) + 4);
      const off = w + 2;
      // Bins as wide as the sampling step, or every other row would be empty at 0 degrees (aliasing).
      for (let i = 0; i < pts.length; i += 2) bins[Math.round((-pts[i] * s + pts[i + 1] * c + off) / step)]++;
      let sc = 0;
      for (let i = 1; i < bins.length; i++) { const d = bins[i] - bins[i - 1]; sc += d * d; }
      return sc;
    };
    let best = 0, bestScore = -1;
    for (let d = -10; d <= 10; d += 0.5) { const sc = score(d); if (sc > bestScore) { bestScore = sc; best = d; } }
    const coarse = best;
    for (let d = coarse - 0.5; d <= coarse + 0.5; d += 0.1) { const sc = score(d); if (sc > bestScore) { bestScore = sc; best = d; } }
    return best;
  }

  function grayCanvas(g, w, h) {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    const img = ctx.createImageData(w, h);
    for (let i = 0, j = 0; j < g.length; i += 4, j++) {
      const v = g[j] | 0;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  window.ktInternals = { toGray, stretch, threshold, estimateSkew };
  window.ktPreprocess = function (source, opts = {}) {
    const sw = source.width, sh = source.height;
    const scale = Math.min(1, (opts.maxSide || MAX_SIDE) / Math.max(sw, sh));
    let w = Math.round(sw * scale), h = Math.round(sh * scale);
    const c0 = document.createElement('canvas');
    c0.width = w; c0.height = h;
    const ctx0 = c0.getContext('2d', { willReadFrequently: true });
    ctx0.imageSmoothingQuality = 'high';
    ctx0.drawImage(source, 0, 0, w, h);
    let g = stretch(toGray(ctx0, w, h));
    const win = Math.max(15, Math.round(Math.max(w, h) / 40)) | 1;
    const t = opts.t ?? 0.15;
    const angle = estimateSkew(threshold(g, w, h, win, t), w, h);
    if (Math.abs(angle) >= 0.2) {
      const a = (angle * Math.PI) / 180;
      const nw = Math.round(Math.abs(w * Math.cos(a)) + Math.abs(h * Math.sin(a)));
      const nh = Math.round(Math.abs(w * Math.sin(a)) + Math.abs(h * Math.cos(a)));
      const c1 = document.createElement('canvas');
      c1.width = nw; c1.height = nh;
      const ctx1 = c1.getContext('2d', { willReadFrequently: true });
      ctx1.fillStyle = '#fff';
      ctx1.fillRect(0, 0, nw, nh);
      ctx1.translate(nw / 2, nh / 2);
      ctx1.rotate(-a);
      ctx1.drawImage(grayCanvas(g, w, h), -w / 2, -h / 2);
      w = nw; h = nh;
      g = toGray(ctx1, w, h);
    }
    const bin = threshold(g, w, h, win, t);
    const out = new Float32Array(w * h);
    for (let i = 0; i < out.length; i++) out[i] = bin[i] ? 0 : 255;
    return { canvas: grayCanvas(out, w, h), angle, scale, width: w, height: h };
  };
})();
