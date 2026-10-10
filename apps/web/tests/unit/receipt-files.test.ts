import { describe, expect, it } from "vitest";
import {
  MAX_BYTES,
  checkBytes,
  checkFiles,
  checkPages,
  checkSelection,
  isRefusal,
  sniffMime,
} from "@/lib/receipts/files";
import {
  hasMetadata,
  jpegSegments,
  stripJpegMetadata,
  stripMetadata,
  stripPngMetadata,
} from "@/lib/receipts/exif";

const bytes = (...parts: (number[] | string)[]) =>
  new Uint8Array(
    parts.flatMap((p) => (typeof p === "string" ? [...p].map((c) => c.charCodeAt(0)) : p)),
  );

const JPEG_HEAD = [0xff, 0xd8, 0xff, 0xe0];
const PNG_HEAD = "\x89PNG\r\n\x1a\n";

describe("receipt files are checked on the device", () => {
  it("EXTRACT-01 the real type comes from magic bytes, like the server's sniff [FR-10]", () => {
    expect(sniffMime(bytes(JPEG_HEAD))).toBe("image/jpeg");
    expect(sniffMime(bytes(PNG_HEAD))).toBe("image/png");
    expect(sniffMime(bytes("RIFF", [0, 0, 0, 0], "WEBP"))).toBe("image/webp");
    expect(sniffMime(bytes([0, 0, 0, 24], "ftypheic"))).toBe("image/heic");
    expect(sniffMime(bytes([0, 0, 0, 24], "ftypmif1"))).toBe("image/heic");
    expect(sniffMime(bytes("%PDF-1.7\n"))).toBe("application/pdf");
    expect(sniffMime(bytes("\n\n%PDF-1.4"))).toBe("application/pdf"); // junk before the header
    expect(sniffMime(bytes("GIF89a"))).toBeNull();
    expect(sniffMime(bytes("hello, I am a .jpg"))).toBeNull();
  });

  it("EXTRACT-01 over 20 MB, empty and unknown types are refused before any upload [FR-10]", () => {
    expect(checkBytes(bytes(JPEG_HEAD), 1234)).toBe("image/jpeg");
    expect(checkBytes(bytes(JPEG_HEAD), MAX_BYTES)).toBe("image/jpeg");
    expect(checkBytes(bytes(JPEG_HEAD), MAX_BYTES + 1)).toMatchObject({ code: "size" });
    expect(checkBytes(bytes("plain text"), 10)).toMatchObject({ code: "type" });
    expect(checkBytes(bytes(), 0)).toMatchObject({ code: "empty" });
    expect(checkPages(10)).toBeNull();
    expect(checkPages(11)).toMatchObject({
      code: "pages",
      message: "That PDF has more than 10 pages.",
    });
  });

  it("EXTRACT-01 a pick is up to 10 photos or one PDF, never a mix [FR-10]", () => {
    expect(checkSelection(["image/jpeg", "image/png"])).toBeNull();
    expect(checkSelection(["application/pdf"])).toBeNull();
    expect(checkSelection(["application/pdf", "image/jpeg"])).toMatchObject({ code: "mixed" });
    expect(checkSelection(["application/pdf", "application/pdf"])).toMatchObject({ code: "mixed" });
    expect(checkSelection(Array(11).fill("image/jpeg"))).toMatchObject({ code: "count" });
  });

  it("EXTRACT-01 checkFiles reads only the first bytes of each File [FR-10]", async () => {
    const ok = await checkFiles([new File([bytes(JPEG_HEAD, "rest")], "r.heic")]);
    expect(isRefusal(ok)).toBe(false);
    expect(ok).toMatchObject([{ mime: "image/jpeg" }]);
    const renamed = await checkFiles([
      new File(["just text"], "receipt.jpg", { type: "image/jpeg" }),
    ]);
    expect(renamed).toMatchObject({ code: "type" });
    expect(await checkFiles([])).toMatchObject({ code: "empty" });
  });
});

/** A JPEG with APP0, an EXIF APP1 carrying "GPS", an XMP APP1, a comment, an ICC APP2, then scan data. */
function jpegWithMetadata(): Uint8Array {
  const seg = (marker: number, body: number[] | string) => {
    const payload = typeof body === "string" ? [...body].map((c) => c.charCodeAt(0)) : body;
    const len = payload.length + 2;
    return [0xff, marker, len >> 8, len & 0xff, ...payload];
  };
  return new Uint8Array([
    0xff,
    0xd8,
    ...seg(0xe0, "JFIF\0\x01\x01\0\0\x01\0\x01\0\0"),
    ...seg(0xe1, "Exif\0\0MM\0*\0\0\0\x08GPSLatitude40.7128N"),
    ...seg(0xe1, "http://ns.adobe.com/xap/1.0/\0<x:xmpmeta/>"),
    ...seg(0xfe, "shot on a phone"),
    ...seg(0xe2, "ICC_PROFILE\0\x01\x01profile"),
    ...seg(0xdb, [0, ...Array(64).fill(1)]),
    ...seg(0xda, [1, 1, 0, 0, 63, 0]),
    0x12,
    0x34,
    0xff,
    0x00,
    0x56,
    0xff,
    0xd9,
  ]);
}

describe("photo metadata never leaves the device", () => {
  it("EXTRACT-02 EXIF (GPS), XMP and comments are dropped from a JPEG; image data untouched [FR-10]", () => {
    const original = jpegWithMetadata();
    expect(hasMetadata(original)).toBe(true);
    const clean = stripJpegMetadata(original);
    expect(hasMetadata(clean)).toBe(false);
    const text = String.fromCharCode(...clean);
    expect(text).not.toContain("Exif");
    expect(text).not.toContain("GPS");
    expect(text).not.toContain("xmpmeta");
    expect(text).not.toContain("shot on a phone");
    expect(text).toContain("JFIF");
    expect(text).toContain("ICC_PROFILE");
    // Everything from SOS to EOI is copied byte for byte.
    const tail = (b: Uint8Array) => b.slice(b.length - 7);
    expect([...tail(clean)]).toEqual([...tail(original)]);
    expect(jpegSegments(clean)?.segments.map((s) => s.marker)).toEqual([0xe0, 0xe2, 0xdb, 0xda]);
    expect(stripMetadata(clean)).toEqual(clean);
  });

  it("EXTRACT-02 PNG eXIf and text chunks are dropped [FR-10]", () => {
    const chunk = (type: string, data: string) => {
      const len = data.length;
      return [
        len >>> 24,
        (len >> 16) & 255,
        (len >> 8) & 255,
        len & 255,
        ...bytes(type, data),
        0,
        0,
        0,
        0,
      ];
    };
    const png = new Uint8Array([
      ...bytes(PNG_HEAD),
      ...chunk("IHDR", "\0\0\0\x01\0\0\0\x01\x08\0\0\0\0"),
      ...chunk("eXIf", "MM\0*GPS"),
      ...chunk("tEXt", "Comment\0hi"),
      ...chunk("IDAT", "x"),
      ...chunk("IEND", ""),
    ]);
    expect(hasMetadata(png)).toBe(true);
    const clean = stripPngMetadata(png);
    expect(hasMetadata(clean)).toBe(false);
    expect(String.fromCharCode(...clean)).not.toContain("GPS");
    expect(String.fromCharCode(...clean)).toContain("IDAT");
  });

  it("EXTRACT-02 malformed or unknown files come back unchanged [FR-10]", () => {
    const junk = bytes("not an image");
    expect(stripMetadata(junk)).toBe(junk);
    expect(stripJpegMetadata(new Uint8Array([0xff, 0xd8, 0x00]))).toEqual(
      new Uint8Array([0xff, 0xd8, 0x00]),
    );
    expect(hasMetadata(bytes("RIFF", [0, 0, 0, 0], "WEBP", "EXIF", [4, 0, 0, 0], "abcd"))).toBe(
      true,
    );
  });
});
