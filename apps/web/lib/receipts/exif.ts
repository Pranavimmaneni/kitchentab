/**
 * Photo metadata on the device (PRD §9 step 2, EXTRACT-02): EXIF (with GPS), XMP and comments never
 * leave the phone. Photos are re-encoded through a canvas, which writes none, and these byte-level
 * passes are the guard after that (and what the tests read back from storage).
 *
 * JPEG: segments `FF xx <len>` up to SOS; APP1 (Exif, XMP) … APP15 and COM are dropped, APP0 (JFIF),
 * APP2 ICC profiles and APP14 (Adobe colour transform) stay. PNG: `eXIf`, text chunks and `tIME`
 * are dropped. WEBP is only inspected (it is re-encoded to JPEG).
 */

const SOI = 0xd8;
const SOS = 0xda;
const EOI = 0xd9;
const APP0 = 0xe0;
const APP2 = 0xe2;
const APP14 = 0xee;
const COM = 0xfe;

function ascii(bytes: Uint8Array, start: number, length: number): string {
  let out = "";
  for (let i = start; i < Math.min(start + length, bytes.length); i++) out += String.fromCharCode(bytes[i]!);
  return out;
}

export function isJpeg(bytes: Uint8Array): boolean {
  return bytes.length > 3 && bytes[0] === 0xff && bytes[1] === SOI;
}

export function isPng(bytes: Uint8Array): boolean {
  return ascii(bytes, 0, 8) === "\x89PNG\r\n\x1a\n";
}

export function isWebp(bytes: Uint8Array): boolean {
  return ascii(bytes, 0, 4) === "RIFF" && ascii(bytes, 8, 4) === "WEBP";
}

type Segment = { marker: number; start: number; end: number };

/** JPEG header segments up to and including SOS (the scan data after it is not parsed). */
export function jpegSegments(bytes: Uint8Array): { segments: Segment[]; scanStart: number } | null {
  if (!isJpeg(bytes)) return null;
  const segments: Segment[] = [];
  let i = 2;
  while (i < bytes.length) {
    if (bytes[i] !== 0xff) return null; // not a marker: malformed
    while (i < bytes.length && bytes[i] === 0xff) i++; // fill bytes
    const marker = bytes[i];
    if (marker === undefined) return null;
    const start = i - 1;
    i++;
    if (marker === EOI) return { segments, scanStart: bytes.length };
    if ((marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) {
      segments.push({ marker, start, end: i });
      continue;
    }
    if (i + 1 >= bytes.length) return null;
    const length = (bytes[i]! << 8) | bytes[i + 1]!;
    if (length < 2) return null;
    const end = i + length;
    if (end > bytes.length) return null;
    segments.push({ marker, start, end });
    i = end;
    if (marker === SOS) return { segments, scanStart: end };
  }
  return null;
}

function payload(bytes: Uint8Array, seg: Segment): Uint8Array {
  return bytes.subarray(seg.start + 4, seg.end);
}

function keepJpegSegment(bytes: Uint8Array, seg: Segment): boolean {
  if (seg.marker === COM) return false;
  if (seg.marker < APP0 || seg.marker > 0xef) return true; // not an APPn: image data, keep
  if (seg.marker === APP0 || seg.marker === APP14) return true;
  if (seg.marker === APP2) return ascii(payload(bytes, seg), 0, 12) === "ICC_PROFILE\0";
  return false;
}

/** The JPEG without EXIF/XMP/comments; the image data is copied untouched. */
export function stripJpegMetadata(bytes: Uint8Array): Uint8Array {
  const parsed = jpegSegments(bytes);
  if (!parsed) return bytes;
  const parts: Uint8Array[] = [bytes.subarray(0, 2)];
  for (const seg of parsed.segments) {
    if (keepJpegSegment(bytes, seg)) parts.push(bytes.subarray(seg.start, seg.end));
  }
  parts.push(bytes.subarray(parsed.scanStart));
  return concat(parts);
}

const PNG_DROP = new Set(["eXIf", "tEXt", "zTXt", "iTXt", "tIME"]);

type Chunk = { type: string; start: number; end: number };

export function pngChunks(bytes: Uint8Array): Chunk[] | null {
  if (!isPng(bytes)) return null;
  const chunks: Chunk[] = [];
  let i = 8;
  while (i + 8 <= bytes.length) {
    const length = ((bytes[i]! << 24) >>> 0) + (bytes[i + 1]! << 16) + (bytes[i + 2]! << 8) + bytes[i + 3]!;
    const type = ascii(bytes, i + 4, 4);
    const end = i + 12 + length;
    if (end > bytes.length) return null;
    chunks.push({ type, start: i, end });
    i = end;
    if (type === "IEND") break;
  }
  return chunks;
}

/** The PNG without `eXIf`, text chunks and `tIME`. */
export function stripPngMetadata(bytes: Uint8Array): Uint8Array {
  const chunks = pngChunks(bytes);
  if (!chunks) return bytes;
  const parts: Uint8Array[] = [bytes.subarray(0, 8)];
  for (const c of chunks) if (!PNG_DROP.has(c.type)) parts.push(bytes.subarray(c.start, c.end));
  return concat(parts);
}

/** True when the file still carries EXIF or XMP (JPEG APP1, PNG eXIf/text, WEBP EXIF/XMP). */
export function hasMetadata(bytes: Uint8Array): boolean {
  const jpeg = jpegSegments(bytes);
  if (jpeg) return jpeg.segments.some((s) => !keepJpegSegment(bytes, s));
  const png = pngChunks(bytes);
  if (png) return png.some((c) => PNG_DROP.has(c.type) && c.type !== "tIME");
  if (isWebp(bytes)) {
    let i = 12;
    while (i + 8 <= bytes.length) {
      const type = ascii(bytes, i, 4);
      const size = bytes[i + 4]! | (bytes[i + 5]! << 8) | (bytes[i + 6]! << 16) | (bytes[i + 7]! << 24);
      if (type === "EXIF" || type === "XMP ") return true;
      i += 8 + size + (size % 2);
    }
  }
  return false;
}

/** Strips whatever this format can carry; other formats come back as they are. */
export function stripMetadata(bytes: Uint8Array): Uint8Array {
  if (isJpeg(bytes)) return stripJpegMetadata(bytes);
  if (isPng(bytes)) return stripPngMetadata(bytes);
  return bytes;
}

function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const p of parts) {
    out.set(p, offset);
    offset += p.length;
  }
  return out;
}
