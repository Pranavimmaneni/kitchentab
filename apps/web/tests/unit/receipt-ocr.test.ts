import { describe, expect, it } from "vitest";
import { linesOf } from "@/lib/receipts/ocr";
import { estimateSkew, stretch, threshold, toGray, windowFor } from "@/lib/receipts/preprocess";

/** Dark horizontal "text lines" on white, rotated by `deg` around the centre. */
function page(w: number, h: number, deg: number): Float32Array {
  const g = new Float32Array(w * h).fill(240);
  const a = (deg * Math.PI) / 180;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      // Un-rotate the pixel and draw lines every 24 px, 6 px thick, with gaps like words.
      const dx = x - w / 2;
      const dy = y - h / 2;
      const ux = dx * Math.cos(a) + dy * Math.sin(a) + w / 2;
      const uy = -dx * Math.sin(a) + dy * Math.cos(a) + h / 2;
      if (ux > 40 && ux < w - 40 && uy > 30 && uy < h - 30 && uy % 24 < 6 && ux % 50 < 40) g[y * w + x] = 20;
    }
  }
  return g;
}

describe("browser OCR preprocessing (the same math as scripts/receipts/preprocess.js)", () => {
  it("EXTRACT-12 grayscale uses Rec. 601 weights and contrast stretches 1st..99th percentile [FR-11]", () => {
    const rgba = new Uint8ClampedArray([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 10, 20, 30, 255]);
    const g = toGray(rgba, 4, 1);
    expect([...g].map((v) => Math.round(v))).toEqual([76, 150, 29, 18]);
    const flat = new Float32Array(1000).map((_, i) => 100 + (i % 51)); // 100..150
    const s = stretch(flat);
    expect(Math.min(...s)).toBe(0);
    expect(Math.max(...s)).toBe(255);
  });

  it("EXTRACT-12 adaptive threshold marks ink, not flat shadows [FR-11]", () => {
    const w = 60;
    const h = 20;
    const g = new Float32Array(w * h).fill(200);
    for (let x = 20; x < 24; x++) for (let y = 5; y < 15; y++) g[y * w + x] = 30; // a stroke
    for (let x = 40; x < 60; x++) for (let y = 0; y < h; y++) g[y * w + x] = 120; // a flat shadow
    const ink = threshold(g, w, h, windowFor(w, h), 0.15);
    expect(ink[10 * w + 21]).toBe(1);
    expect(ink[10 * w + 50]).toBe(0);
    expect(ink[2 * w + 5]).toBe(0);
    expect(windowFor(2000, 1000)).toBe(51);
    expect(windowFor(100, 100) % 2).toBe(1);
  });

  it("EXTRACT-12 deskew finds the rotation of a tilted receipt within half a degree [FR-11]", () => {
    for (const deg of [-5, 0, 3.5]) {
      const w = 420;
      const h = 320;
      const ink = threshold(page(w, h, deg), w, h, windowFor(w, h), 0.15);
      expect(Math.abs(estimateSkew(ink, w, h) - deg)).toBeLessThanOrEqual(0.5);
    }
    expect(estimateSkew(new Uint8Array(100), 10, 10)).toBe(0); // too little ink: no guess
  });

  it("EXTRACT-12 tesseract blocks become the API's flat line list with boxes and confidences [FR-11, FR-12]", () => {
    const lines = linesOf({
      blocks: [
        {
          paragraphs: [
            {
              lines: [
                {
                  text: "WHOLE MILK 1 GAL      4.29\n",
                  bbox: { x0: 10, y0: 20, x1: 300, y1: 40 },
                  confidence: 91.234,
                  words: [{ text: "WHOLE", bbox: { x0: 10, y0: 20, x1: 60, y1: 40 }, confidence: 95.55 }],
                },
                { text: "   \n", bbox: { x0: 0, y0: 0, x1: 1, y1: 1 }, confidence: 0 },
              ],
            },
          ],
        },
      ],
    });
    expect(lines).toEqual([
      {
        text: "WHOLE MILK 1 GAL      4.29",
        bbox: [10, 20, 300, 40],
        confidence: 91.2,
        words: [{ text: "WHOLE", bbox: [10, 20, 60, 40], confidence: 95.6 }],
      },
    ]);
    expect(linesOf({ blocks: null })).toEqual([]);
  });
});
